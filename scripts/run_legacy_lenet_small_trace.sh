#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="${WORK_DIR:-$HOME/build/nvdla-peta/vp-modern}"
SOURCES_DIR="${SOURCES_DIR:-$ROOT/.external/sources}"
DOCKER_IMAGE="${DOCKER_IMAGE:-nvdla/vp:latest}"
VP_TIMEOUT="${VP_TIMEOUT:-1200}"
VP_TRACE_VERBOSITY="${VP_TRACE_VERBOSITY:-sc_high}"
EXPECTED_OUTPUT="${EXPECTED_OUTPUT:-0 2 0 0 0 0 0 124 0 0}"
LENET_DIR="${LENET_DIR:-$ROOT/artifacts/workloads/lenet_small}"
LOADABLE="${LENET_LOADABLE:-$LENET_DIR/lenet_mnist.nv_small.nvdla}"
IMAGE="${LENET_IMAGE:-$LENET_DIR/seven.pgm}"
VP_BINARY="${VP_BINARY:-$WORK_DIR/vp-small/install/bin/aarch64_toplevel}"
VP_LIBRARY_DIR="${VP_LIBRARY_DIR:-$WORK_DIR/vp-small/install/lib}"
VP_CMOD_LIBRARY_DIR="${VP_CMOD_LIBRARY_DIR:-$WORK_DIR/vp-small/hw/outdir/nv_small/cmod/release/lib}"
SYSTEMC_PREFIX="${SYSTEMC_PREFIX:-/usr/local/systemc-2.3.0}"

require_file() {
    if [[ ! -f "$1" ]]; then
        echo "missing required file: $1" >&2
        exit 2
    fi
}

require_dir() {
    if [[ ! -d "$1" ]]; then
        echo "missing required directory: $1" >&2
        exit 2
    fi
}

resolve_docker_bin() {
    local candidate
    for candidate in \
        "${DOCKER_BIN:-}" \
        "$(command -v docker 2>/dev/null || true)" \
        "$(command -v docker.exe 2>/dev/null || true)" \
        "/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe"; do
        if [[ -n "$candidate" && -x "$candidate" ]] && "$candidate" version >/dev/null 2>&1; then
            echo "$candidate"
            return
        fi
    done
    echo "missing usable Docker command" >&2
    exit 2
}

hash_file() {
    sha256sum "$1" | awk '{print $1}'
}

repair_rootfs() {
    local image="$1"
    local log="$2"
    local status=0
    e2fsck -p "$image" >>"$log" 2>&1 || status=$?
    if [[ "$status" -gt 1 ]]; then
        echo "rootfs filesystem repair failed with status $status: $image" >&2
        exit 2
    fi
}

require_file "$ROOT/repro.lock.json"
require_file "$LOADABLE"
require_file "$IMAGE"
require_file "$VP_BINARY"
require_dir "$VP_LIBRARY_DIR"
require_dir "$VP_CMOD_LIBRARY_DIR"
command -v debugfs >/dev/null 2>&1 || { echo "debugfs is required (install e2fsprogs)" >&2; exit 2; }
command -v e2fsck >/dev/null 2>&1 || { echo "e2fsck is required (install e2fsprogs)" >&2; exit 2; }

REGISTER_HEADER="${NVDLA_REGISTER_HEADER:-$SOURCES_DIR/nvdla-sw/kmd/firmware/include/opendla_small.h}"
if [[ ! -f "$REGISTER_HEADER" ]]; then
    REGISTER_HEADER="$ROOT/.work/nvdla-sw-patched/kmd/firmware/include/opendla_small.h"
fi
require_file "$REGISTER_HEADER"

DOCKER_BIN_RESOLVED="$(resolve_docker_bin)"
PINNED_IMAGE_ID="$(PYTHONPATH="$ROOT/tools:${PYTHONPATH:-}" python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["docker"]["vp_latest"]["image_id"])' "$ROOT/repro.lock.json")"
ACTUAL_IMAGE_ID="$("$DOCKER_BIN_RESOLVED" image inspect --format '{{.Id}}' "$DOCKER_IMAGE")"
if [[ "${ACTUAL_IMAGE_ID,,}" != "${PINNED_IMAGE_ID,,}" ]]; then
    echo "Docker image ID mismatch for $DOCKER_IMAGE" >&2
    echo "expected: $PINNED_IMAGE_ID" >&2
    echo "actual:   $ACTUAL_IMAGE_ID" >&2
    exit 2
fi

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-vp-trace-reference-small"
OUT="$ROOT/artifacts/$RUN_ID"
STOCK="$OUT/stock"
PAYLOAD="$OUT/payload"
mkdir -p "$STOCK" "$PAYLOAD"

container_id="$("$DOCKER_BIN_RESOLVED" create "$DOCKER_IMAGE")"
cleanup_container() {
    "$DOCKER_BIN_RESOLVED" rm -f "$container_id" >/dev/null 2>&1 || true
}
trap cleanup_container EXIT
for name in Image rootfs.ext4 drm.ko opendla_2.ko nvdla_runtime libnvdla_runtime.so; do
    "$DOCKER_BIN_RESOLVED" cp "$container_id:/usr/local/nvdla/$name" "$STOCK/$name"
done
cleanup_container
trap - EXIT

cp "$STOCK/drm.ko" "$PAYLOAD/drm.ko"
cp "$STOCK/opendla_2.ko" "$PAYLOAD/opendla_2.ko"
cp "$STOCK/nvdla_runtime" "$PAYLOAD/nvdla_runtime"
cp "$STOCK/libnvdla_runtime.so" "$PAYLOAD/libnvdla_runtime.so"
cp "$LOADABLE" "$PAYLOAD/loadable.nvdla"
cp "$IMAGE" "$PAYLOAD/seven.pgm"

cat >"$PAYLOAD/run-reference-trace.sh" <<'EOF'
#!/bin/sh
set +e
mkdir -p /mnt/w
mount -t 9p -o trans=virtio,version=9p2000.L w /mnt/w || mount -t 9p -o trans=virtio w /mnt/w
WRITE_STATUS=$?

echo "__NVDLA_REFERENCE_BEGIN__"
insmod /mnt/r/drm.ko >/tmp/drm-load.log 2>&1
DRM_STATUS=$?
insmod /mnt/r/opendla_2.ko >/tmp/module-load.log 2>&1
MODULE_STATUS=$?
sleep 1
ls -l /dev/dri >/tmp/dev-dri.txt 2>&1
DRI_STATUS=$?

cd /tmp
rm -f output.dimg
if [ "$DRM_STATUS" -eq 0 ] && [ "$MODULE_STATUS" -eq 0 ] && [ "$DRI_STATUS" -eq 0 ]; then
    LD_LIBRARY_PATH=/mnt/r /mnt/r/nvdla_runtime \
        --loadable /mnt/r/loadable.nvdla \
        --image /mnt/r/seven.pgm \
        --rawdump >/tmp/runtime.log 2>&1
    RUNTIME_STATUS=$?
else
    echo "drm=$DRM_STATUS module=$MODULE_STATUS dri=$DRI_STATUS" >/tmp/runtime.log
    RUNTIME_STATUS=98
fi

if [ -f /tmp/output.dimg ]; then
    cat /tmp/output.dimg >/tmp/output.txt
else
    : >/tmp/output.txt
fi
dmesg >/tmp/dmesg.log 2>&1

for file in drm-load.log module-load.log dev-dri.txt runtime.log output.dimg output.txt dmesg.log; do
    if [ "$WRITE_STATUS" -eq 0 ] && [ -f "/tmp/$file" ]; then
        cp "/tmp/$file" "/mnt/w/$file"
    fi
done

echo "__NVDLA_SECTION_runtime_BEGIN__"
cat /tmp/runtime.log
echo "__NVDLA_SECTION_runtime_END__"
echo "__NVDLA_SECTION_output_BEGIN__"
cat /tmp/output.txt
echo "__NVDLA_SECTION_output_END__"
echo "__NVDLA_RESULT__ drm=$DRM_STATUS module=$MODULE_STATUS dri=$DRI_STATUS runtime=$RUNTIME_STATUS"
echo "__NVDLA_REFERENCE_END__"
sync
if [ "$DRM_STATUS" -eq 0 ] && [ "$MODULE_STATUS" -eq 0 ] && [ "$DRI_STATUS" -eq 0 ] && [ "$RUNTIME_STATUS" -eq 0 ]; then
    exit 0
fi
exit 1
EOF
chmod +x "$PAYLOAD/run-reference-trace.sh"

cat >"$OUT/S99nvdla-reference" <<'EOF'
#!/bin/sh
case "$1" in
    start)
        echo "__NVDLA_AUTORUN_BEGIN__"
        mkdir -p /mnt/r
        mount -t 9p -o trans=virtio,version=9p2000.L r /mnt/r || mount -t 9p -o trans=virtio r /mnt/r
        sh /mnt/r/run-reference-trace.sh
        STATUS=$?
        echo "__NVDLA_AUTORUN_STATUS__=$STATUS"
        poweroff -f
        ;;
esac
EOF
chmod +x "$OUT/S99nvdla-reference"

cp "$STOCK/rootfs.ext4" "$OUT/rootfs-reference.ext4"
: >"$OUT/rootfs-fsck.log"
repair_rootfs "$OUT/rootfs-reference.ext4" "$OUT/rootfs-fsck.log"
debugfs -w -R "write $OUT/S99nvdla-reference /etc/init.d/S99nvdla-reference" "$OUT/rootfs-reference.ext4" >"$OUT/rootfs-inject.log" 2>&1
debugfs -w -R "set_inode_field /etc/init.d/S99nvdla-reference mode 0100755" "$OUT/rootfs-reference.ext4" >>"$OUT/rootfs-inject.log" 2>&1
repair_rootfs "$OUT/rootfs-reference.ext4" "$OUT/rootfs-fsck.log"

cat >"$OUT/reference-vp.lua" <<'EOF'
CPU = {
    library = "libqbox-nvdla.so",
    extra_arguments = '-machine virt -cpu cortex-a57 -machine type=virt -nographic -smp 1 -m 1024 -net none -kernel /vp-stock/Image --append "root=/dev/vda" -drive file=/vp-run/rootfs-reference.ext4,if=none,format=raw,id=hd0,snapshot=on -device virtio-blk-device,drive=hd0 -fsdev local,id=r,path=/payload,security_model=none -device virtio-9p-device,fsdev=r,mount_tag=r -fsdev local,id=w,path=/vp-run,security_model=none -device virtio-9p-device,fsdev=w,mount_tag=w'
}

ram = {
    size = 1048576,
    target_port = { base_addr = 0xc0000000, high_addr = 0xffffffff }
}

nvdla = {
    irq_number = 176,
    csb_port = { base_addr = 0x10200000, high_addr = 0x1021ffff }
}
EOF

VP_BINARY_DIR="$(dirname "$VP_BINARY")"
VP_BINARY_BASENAME="$(basename "$VP_BINARY")"
SC_LOG_VALUE="outfile:/vp-run/systemc.log;verbosity_level:$VP_TRACE_VERBOSITY;csb_adaptor:enable;dbb_adaptor:enable"
cleanup_run_container() {
    if [[ -s "$OUT/docker.cid" ]]; then
        "$DOCKER_BIN_RESOLVED" rm -f "$(cat "$OUT/docker.cid")" >/dev/null 2>&1 || true
    fi
}
trap cleanup_run_container EXIT
set +e
timeout -k 30 "$VP_TIMEOUT" "$DOCKER_BIN_RESOLVED" run --rm -i \
    --cidfile "$OUT/docker.cid" \
    -e SC_SIGNAL_WRITE_CHECK=DISABLE \
    -e SC_LOG="$SC_LOG_VALUE" \
    -v "$OUT:/vp-run" \
    -v "$STOCK:/vp-stock:ro" \
    -v "$PAYLOAD:/payload:ro" \
    -v "$VP_BINARY_DIR:/vp-small-bin:ro" \
    -v "$VP_LIBRARY_DIR:/vp-small-lib:ro" \
    -v "$VP_CMOD_LIBRARY_DIR:/vp-small-cmod:ro" \
    -w /vp-run \
    "$DOCKER_IMAGE" \
    bash -lc "set +e; export LD_LIBRARY_PATH=/vp-small-lib:/vp-small-cmod:$SYSTEMC_PREFIX/lib-linux64:\${LD_LIBRARY_PATH:-}; /vp-small-bin/$VP_BINARY_BASENAME -c /vp-run/reference-vp.lua; status=\$?; echo __NVDLA_VP_PROCESS_STATUS__=\$status; if [ \$status -eq 139 ] && [ -f /vp-run/output.txt ]; then exit 0; fi; exit \$status" \
    2>&1 | tee "$OUT/serial.log"
RUN_STATUS=${PIPESTATUS[0]}
set -e
cleanup_run_container
trap - EXIT

require_file "$OUT/systemc.log"
set +e
(cd /tmp && PYTHONPATH="$ROOT/tools:${PYTHONPATH:-}" python3 -m nvdla_test_framework trace-parse \
    --input "$OUT/systemc.log" \
    --register-header "$REGISTER_HEADER" \
    --csb-out "$OUT/csb-events.jsonl" \
    --raw-csb-out "$OUT/csb.raw.log" \
    --raw-dbb-out "$OUT/dbb.raw.log" \
    --summary-out "$OUT/trace-summary.json")
TRACE_STATUS=$?
set -e

VP_PROCESS_STATUS="$(sed -n 's/^__NVDLA_VP_PROCESS_STATUS__=//p' "$OUT/serial.log" | tail -n 1)"
VP_PROCESS_STATUS="${VP_PROCESS_STATUS:-$RUN_STATUS}"
VP_TEARDOWN_ONLY=false
if [[ "$VP_PROCESS_STATUS" -eq 139 ]] \
    && grep -q '__NVDLA_REFERENCE_END__' "$OUT/serial.log" \
    && grep -q 'reboot: Power down' "$OUT/serial.log"; then
    VP_TEARDOWN_ONLY=true
    grep -E 'reboot: Power down|Segmentation fault|__NVDLA_VP_PROCESS_STATUS__' "$OUT/serial.log" >"$OUT/vp-teardown.log" || true
else
    : >"$OUT/vp-teardown.log"
fi

OUTPUT_NORMALIZED=""
if [[ -f "$OUT/output.txt" ]]; then
    OUTPUT_NORMALIZED="$(tr -s '[:space:]' ' ' <"$OUT/output.txt" | sed 's/^ //; s/ $//')"
fi
LAYER_COMPLETIONS="$(grep -Eo '[0-9]+ HWLs done, *totally 10 layers' "$OUT/serial.log" \
    | awk '{ if ($1 > maximum) maximum = $1 } END { print maximum + 0 }')"
BAD_PATTERNS='Oops|BUG|WARNING|DMA-API|scheduler timeout|interrupt timeout|TLM_ADDRESS_ERROR_RESPONSE|TLM_GENERIC_ERROR_RESPONSE|SC_REPORT_FATAL|invalid configuration'
grep -E "$BAD_PATTERNS" "$OUT/serial.log" "$OUT/dmesg.log" "$OUT/systemc.log" >"$OUT/bad-patterns.log" 2>/dev/null || true

STATUS="blocked"
CLASSIFICATION="reference_invalid"
VP_EXIT_ACCEPTED=false
if [[ "$VP_PROCESS_STATUS" -eq 0 || "$VP_TEARDOWN_ONLY" == true ]]; then
    VP_EXIT_ACCEPTED=true
fi
if [[ "$RUN_STATUS" -eq 0 && "$TRACE_STATUS" -eq 0 && "$VP_EXIT_ACCEPTED" == true \
    && "$OUTPUT_NORMALIZED" == "$EXPECTED_OUTPUT" && "$LAYER_COMPLETIONS" -eq 10 && ! -s "$OUT/bad-patterns.log" ]]; then
    STATUS="pass"
    CLASSIFICATION="pass"
fi

CMOD_LIBRARY="$VP_CMOD_LIBRARY_DIR/libnvdla_cmod.so"
require_file "$CMOD_LIBRARY"
cat >"$OUT/manifest.json" <<EOF
{
  "schema_version": 1,
  "run_id": "$RUN_ID",
  "lane": "vp-reference",
  "mode": "trace_lenet_small",
  "status": "$STATUS",
  "classification": "$CLASSIFICATION",
  "vp_hw_config": "small",
  "runner_status": $RUN_STATUS,
  "vp_process_status": $VP_PROCESS_STATUS,
  "vp_teardown_only": $VP_TEARDOWN_ONLY,
  "trace_status": $TRACE_STATUS,
  "trace_verbosity": "$VP_TRACE_VERBOSITY",
  "expected_output": "$EXPECTED_OUTPUT",
  "actual_output": "$OUTPUT_NORMALIZED",
  "layer_completion_count": $LAYER_COMPLETIONS,
  "docker": {
    "image": "$DOCKER_IMAGE",
    "image_id": "$ACTUAL_IMAGE_ID"
  },
  "inputs": {
    "vp_binary": {"path": "$VP_BINARY", "sha256": "$(hash_file "$VP_BINARY")"},
    "cmod": {"path": "$CMOD_LIBRARY", "sha256": "$(hash_file "$CMOD_LIBRARY")"},
    "kernel": {"sha256": "$(hash_file "$STOCK/Image")"},
    "rootfs": {"sha256": "$(hash_file "$STOCK/rootfs.ext4")"},
    "drm_module": {"sha256": "$(hash_file "$STOCK/drm.ko")"},
    "nvdla_module": {"sha256": "$(hash_file "$STOCK/opendla_2.ko")"},
    "runtime": {"sha256": "$(hash_file "$STOCK/nvdla_runtime")"},
    "runtime_library": {"sha256": "$(hash_file "$STOCK/libnvdla_runtime.so")"},
    "loadable": {"path": "$LOADABLE", "sha256": "$(hash_file "$LOADABLE")", "config": "nv_small"},
    "image": {"path": "$IMAGE", "sha256": "$(hash_file "$IMAGE")"},
    "register_map": {"path": "$REGISTER_HEADER", "sha256": "$(hash_file "$REGISTER_HEADER")"}
  },
  "artifacts": {
    "systemc": "systemc.log",
    "csb_raw": "csb.raw.log",
    "dbb_raw": "dbb.raw.log",
    "csb_events": "csb-events.jsonl",
    "trace_summary": "trace-summary.json",
    "serial": "serial.log",
    "dmesg": "dmesg.log",
    "runtime": "runtime.log",
    "output": "output.txt",
    "bad_patterns": "bad-patterns.log",
    "vp_teardown": "vp-teardown.log"
  }
}
EOF

printf '%s\n' "$OUT" >"$ROOT/artifacts/latest-vp-trace-reference-small.txt"
echo "Legacy nv_small trace reference: $STATUS ($CLASSIFICATION)"
echo "Artifacts: $OUT"
[[ "$STATUS" == "pass" ]]
