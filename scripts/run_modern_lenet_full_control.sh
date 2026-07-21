#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="${WORK_DIR:-$HOME/build/nvdla-peta/vp-modern}"
SOURCES_DIR="${SOURCES_DIR:-$ROOT/.external/sources}"
LENET_DIR="${LENET_DIR:-$ROOT/artifacts/20260703T115149Z-vp-stock-lenet}"
DOCKER_IMAGE="${DOCKER_IMAGE:-nvdla/vp:latest}"
VP_TIMEOUT="${VP_TIMEOUT:-900}"
VP_HW_CONFIG="${VP_HW_CONFIG:-full}"
REPEAT="${REPEAT:-1}"
VP_TRACE="${VP_TRACE:-0}"
VP_TRACE_VERBOSITY="${VP_TRACE_VERBOSITY:-sc_high}"

case "$VP_HW_CONFIG" in
    full)
        VP_RAM_BASE="${VP_RAM_BASE:-0x40000000}"
        VP_RAM_HIGH="${VP_RAM_HIGH:-0x7fffffff}"
        RUN_SUFFIX="vp-modern-lenet-full"
        MODE_NAME="lenet_full_control"
        LOADABLE_CONFIG="nv_full"
        DEFAULT_LOADABLE="$LENET_DIR/lenet_mnist.nv_full.nvdla"
        VP_RUNNER="${VP_RUNNER:-docker}"
        ;;
    small)
        VP_RAM_BASE="${VP_RAM_BASE:-0xc0000000}"
        VP_RAM_HIGH="${VP_RAM_HIGH:-0xffffffff}"
        RUN_SUFFIX="vp-modern-lenet-small"
        MODE_NAME="lenet_small_control"
        LOADABLE_CONFIG="nv_small"
        DEFAULT_LOADABLE="$LENET_DIR/lenet_mnist.nv_small.nvdla"
        if [[ -z "${LENET_LOADABLE:-}" && ! -f "$DEFAULT_LOADABLE" ]]; then
            if [[ -f "$LENET_DIR/lenet_mnist.local.nvdla" ]]; then
                DEFAULT_LOADABLE="$LENET_DIR/lenet_mnist.local.nvdla"
            elif [[ -f "$LENET_DIR/lenet_mnist.prebuilt.nvdla" ]]; then
                DEFAULT_LOADABLE="$LENET_DIR/lenet_mnist.prebuilt.nvdla"
            fi
        fi
        VP_RUNNER="${VP_RUNNER:-source-docker}"
        ;;
    *)
        echo "unsupported VP_HW_CONFIG=$VP_HW_CONFIG; expected full or small" >&2
        exit 2
        ;;
esac

if [[ "$VP_TRACE" == "1" ]]; then
    if [[ "$VP_HW_CONFIG" != "small" ]]; then
        echo "VP_TRACE currently supports only VP_HW_CONFIG=small" >&2
        exit 2
    fi
    RUN_SUFFIX="vp-trace-modern-small"
    MODE_NAME="trace_lenet_small"
fi

KERNEL_IMAGE="${VP_MODERN_KERNEL:-$WORK_DIR/kernel/arch/arm64/boot/Image.vp2m}"
ROOTFS_IMAGE="${VP_MODERN_ROOTFS:-$WORK_DIR/buildroot/images/rootfs-smoke.ext4}"
KMOD="${VP_MODERN_KO:-$WORK_DIR/modules/opendla.ko}"
RUNTIME_BIN="${VP_RUNTIME_BIN:-$WORK_DIR/runtime/nvdla_runtime}"
RUNTIME_LIB="${VP_RUNTIME_LIB:-$WORK_DIR/runtime/libnvdla_runtime.so}"
DTB_IMAGE="${VP_MODERN_DTB:-}"
RCU_CPU_STALL_TIMEOUT="${VP_RCU_CPU_STALL_TIMEOUT:-}"
VP_BINARY="${VP_BINARY:-$WORK_DIR/vp-small/install/bin/aarch64_toplevel}"
SYSTEMC_PREFIX="${SYSTEMC_PREFIX:-${VP_SYSTEMC_PREFIX:-/usr/local/systemc-2.3.0}}"
VP_BINARY_DIR="$(dirname "$VP_BINARY")"
VP_BINARY_BASENAME="$(basename "$VP_BINARY")"
VP_LIBRARY_DIR="${VP_LIBRARY_DIR:-$WORK_DIR/vp-small/install/lib}"
VP_CMOD_LIBRARY_DIR="${VP_CMOD_LIBRARY_DIR:-$WORK_DIR/vp-small/hw/outdir/nv_small/cmod/release/lib}"
VP_LD_LIBRARY_PATH="${VP_LD_LIBRARY_PATH:-$VP_LIBRARY_DIR:$VP_CMOD_LIBRARY_DIR:$SYSTEMC_PREFIX/lib-linux64:$SYSTEMC_PREFIX/lib}"

LOADABLE="${LENET_LOADABLE:-$DEFAULT_LOADABLE}"
IMAGE="$LENET_DIR/seven.pgm"
EXPECTED_OUTPUT="${EXPECTED_OUTPUT:-0 2 0 0 0 0 0 124 0 0}"

require_file() {
    local path="$1"
    if [[ ! -f "$path" ]]; then
        echo "missing required file: $path" >&2
        exit 2
    fi
}

require_dir() {
    local path="$1"
    if [[ ! -d "$path" ]]; then
        echo "missing required directory: $path" >&2
        exit 2
    fi
}

if [[ -n "${EXPECTED_OUTPUT_FILE:-}" ]]; then
    require_file "$EXPECTED_OUTPUT_FILE"
    EXPECTED_OUTPUT="$(tr -s '[:space:]' ' ' <"$EXPECTED_OUTPUT_FILE" | sed 's/^ //; s/ $//')"
fi
if ! [[ "$REPEAT" =~ ^[0-9]+$ ]] || [[ "$REPEAT" -lt 1 ]]; then
    echo "REPEAT must be a positive integer, got $REPEAT" >&2
    exit 2
fi

case "$VP_RUNNER" in
    docker|source-docker|host)
        ;;
    *)
        echo "unsupported VP_RUNNER=$VP_RUNNER; expected docker, source-docker, or host" >&2
        exit 2
        ;;
esac

require_file "$KERNEL_IMAGE"
require_file "$ROOTFS_IMAGE"
require_file "$KMOD"
require_file "$RUNTIME_BIN"
require_file "$RUNTIME_LIB"
require_file "$LOADABLE"
require_file "$IMAGE"
if [[ -n "$DTB_IMAGE" ]]; then
    require_file "$DTB_IMAGE"
fi
if [[ "$VP_RUNNER" == "host" || "$VP_RUNNER" == "source-docker" ]]; then
    require_file "$VP_BINARY"
    require_dir "$VP_LIBRARY_DIR"
    require_dir "$VP_CMOD_LIBRARY_DIR"
fi

resolve_docker_bin() {
    if [[ -n "${DOCKER_BIN:-}" ]]; then
        if ! "$DOCKER_BIN" version >/dev/null 2>&1; then
            echo "configured DOCKER_BIN is not usable: $DOCKER_BIN" >&2
            exit 2
        fi
        echo "$DOCKER_BIN"
        return
    fi
    local candidate
    for candidate in \
        "$(command -v docker 2>/dev/null || true)" \
        "$(command -v docker.exe 2>/dev/null || true)" \
        "/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe"; do
        if [[ -n "$candidate" && -x "$candidate" ]] && "$candidate" version >/dev/null 2>&1; then
            echo "$candidate"
            return
        fi
    done
    echo "missing docker command; set DOCKER_BIN=/path/to/docker or enable Docker Desktop WSL integration" >&2
    exit 2
}

hash_file() {
    sha256sum "$1" | awk '{print $1}'
}

hash_patch_series() {
    find "$ROOT/patches/nvdla-sw" -type f -name '*.patch' -print0 \
        | sort -z \
        | xargs -0 sha256sum \
        | sha256sum \
        | awk '{print $1}'
}

KERNEL_SHA="$(hash_file "$KERNEL_IMAGE")"
ROOTFS_SHA="$(hash_file "$ROOTFS_IMAGE")"
KMOD_SHA="$(hash_file "$KMOD")"
RUNTIME_BIN_SHA="$(hash_file "$RUNTIME_BIN")"
RUNTIME_LIB_SHA="$(hash_file "$RUNTIME_LIB")"
LOADABLE_SHA="$(hash_file "$LOADABLE")"
IMAGE_SHA="$(hash_file "$IMAGE")"
DTB_SHA=""
DTB_ARG=""
DOCKER_DTB_MOUNT=()
if [[ -n "$DTB_IMAGE" ]]; then
    DTB_SHA="$(hash_file "$DTB_IMAGE")"
    DTB_ARG=" -dtb /vp-dtb/$(basename "$DTB_IMAGE")"
    DOCKER_DTB_MOUNT=(-v "$(dirname "$DTB_IMAGE"):/vp-dtb:ro")
fi
PATCH_SERIES_SHA="$(hash_patch_series)"
KMOD_VERMAGIC="$(modinfo -F vermagic "$KMOD" 2>/dev/null || true)"
DOCKER_BIN_RESOLVED=""
DOCKER_IMAGE_ID=""
if [[ "$VP_RUNNER" == "docker" || "$VP_RUNNER" == "source-docker" ]]; then
    DOCKER_BIN_RESOLVED="$(resolve_docker_bin)"
    DOCKER_IMAGE_ID="$("$DOCKER_BIN_RESOLVED" image inspect --format '{{.Id}}' "$DOCKER_IMAGE" 2>/dev/null || true)"
fi

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$RUN_SUFFIX"
OUT="$ROOT/artifacts/$RUN_ID"
PAYLOAD="$OUT/payload"
mkdir -p "$PAYLOAD"

TRACE_DOCKER_ENV=()
TRACE_HOST_ENV=()
SC_LOG_VALUE=""
if [[ "$VP_TRACE" == "1" ]]; then
    SC_LOG_VALUE="outfile:/vp-run/systemc.log;verbosity_level:$VP_TRACE_VERBOSITY;csb_adaptor:enable;dbb_adaptor:enable"
    TRACE_DOCKER_ENV=(-e "SC_LOG=$SC_LOG_VALUE")
    TRACE_HOST_ENV=("SC_LOG=outfile:$OUT/systemc.log;verbosity_level:$VP_TRACE_VERBOSITY;csb_adaptor:enable;dbb_adaptor:enable")
fi

cp "$KMOD" "$PAYLOAD/opendla.ko"
cp "$RUNTIME_BIN" "$PAYLOAD/nvdla_runtime"
cp "$RUNTIME_LIB" "$PAYLOAD/libnvdla_runtime.so"
cp "$LOADABLE" "$PAYLOAD/loadable.nvdla"
cp "$IMAGE" "$PAYLOAD/seven.pgm"
printf '%s\n' "$REPEAT" >"$PAYLOAD/repeat-count"
if [[ -n "$RCU_CPU_STALL_TIMEOUT" ]]; then
    printf '%s\n' "$RCU_CPU_STALL_TIMEOUT" >"$PAYLOAD/rcu-cpu-stall-timeout"
fi

if [[ "$VP_RUNNER" == "docker" || "$VP_RUNNER" == "source-docker" ]]; then
    KERNEL_ARG="/vp-kernel/$(basename "$KERNEL_IMAGE")"
    ROOTFS_ARG="/vp-rootfs/$(basename "$ROOTFS_IMAGE")"
    PAYLOAD_ARG="/payload"
    OUT_ARG="/vp-run"
    DTB_RUNTIME_ARG="$DTB_ARG"
else
    KERNEL_ARG="$KERNEL_IMAGE"
    ROOTFS_ARG="$ROOTFS_IMAGE"
    PAYLOAD_ARG="$PAYLOAD"
    OUT_ARG="$OUT"
    DTB_RUNTIME_ARG=""
    if [[ -n "$DTB_IMAGE" ]]; then
        DTB_RUNTIME_ARG=" -dtb $DTB_IMAGE"
    fi
fi

cat >"$OUT/modern-vp.lua" <<EOF
CPU = {
    library = "libqbox-nvdla.so",
    extra_arguments = '-machine virt -cpu cortex-a57 -machine type=virt -nographic -smp 1 -m 1024 -kernel $KERNEL_ARG$DTB_RUNTIME_ARG --append "root=/dev/vda" -drive file=$ROOTFS_ARG,if=none,format=raw,id=hd0,snapshot=on -device virtio-blk-device,drive=hd0 -fsdev local,id=r,path=$PAYLOAD_ARG,security_model=none -device virtio-9p-device,fsdev=r,mount_tag=r -fsdev local,id=w,path=$OUT_ARG,security_model=none -device virtio-9p-device,fsdev=w,mount_tag=w -netdev user,id=user0,hostfwd=tcp::6666-:6666,hostfwd=tcp::6667-:22 -device virtio-net-device,netdev=user0'
}

ram = {
    size = 1048576,
    target_port = {
        base_addr = $VP_RAM_BASE,
        high_addr = $VP_RAM_HIGH
    }
}

nvdla = {
    irq_number = 176,
    csb_port = {
        base_addr = 0x10200000,
        high_addr = 0x1021ffff
    }
}
EOF

cat >"$PAYLOAD/run-modern-smoke.sh" <<'EOF'
#!/bin/sh
set +e

cat_section() {
    name="$1"
    file="$2"
    echo "__NVDLA_SECTION_${name}_BEGIN__"
    if [ -f "$file" ]; then
        cat "$file"
    fi
    echo "__NVDLA_SECTION_${name}_END__"
}

dump_dt_summary() {
    {
        echo "root.compatible:"
        if [ -e /proc/device-tree/compatible ]; then
            tr '\000' ' ' </proc/device-tree/compatible
            echo
        fi
        for node in \
            /proc/device-tree/memory \
            /proc/device-tree/nvdla@10200000 \
            /proc/device-tree/extmem@c0000000 \
            /proc/device-tree/reserved-memory \
            /proc/device-tree/reserved-memory/*; do
            [ -d "$node" ] || continue
            echo "node: $node"
            for prop in compatible reg interrupts memory-region reusable no-map linux,cma-default linux,dma-default; do
                [ -e "$node/$prop" ] || continue
                printf "  %s:" "$prop"
                od -An -tx1 -v "$node/$prop"
            done
        done
    } >/tmp/device-tree-summary.txt
}

echo "__NVDLA_RUNTIME_BEGIN__"
repeat=1
if [ -r /mnt/r/repeat-count ]; then
    repeat="$(cat /mnt/r/repeat-count)"
fi

if [ -r /mnt/r/rcu-cpu-stall-timeout ] && [ -w /sys/module/rcupdate/parameters/rcu_cpu_stall_timeout ]; then
    cat /mnt/r/rcu-cpu-stall-timeout >/sys/module/rcupdate/parameters/rcu_cpu_stall_timeout
fi
cat /sys/module/rcupdate/parameters/rcu_cpu_stall_timeout >/tmp/rcu-cpu-stall-timeout.txt 2>/dev/null

mkdir -p /mnt/w
mount -t 9p -o trans=virtio,version=9p2000.L w /mnt/w || mount -t 9p -o trans=virtio w /mnt/w
WRITE_STATUS=$?
echo "__NVDLA_STATUS_writable_mount=$WRITE_STATUS"
if [ "$WRITE_STATUS" -eq 0 ]; then
    mkdir -p /mnt/w/runtime-output
fi

insmod /mnt/r/opendla.ko >/tmp/module-load.log 2>&1
MODULE_STATUS=$?
cat_section module_load /tmp/module-load.log
echo "__NVDLA_STATUS_module_load=$MODULE_STATUS"

sleep 1
ls -l /dev/dri >/tmp/dev-dri.txt 2>&1
DRI_STATUS=$?
cat_section dev_dri /tmp/dev-dri.txt
echo "__NVDLA_STATUS_dev_dri=$DRI_STATUS"

NODE="${NVDLA_DEVICE_NODE:-}"
if [ -z "$NODE" ]; then
    NODE="$(ls /dev/dri/renderD* 2>/dev/null | head -n 1)"
fi
echo "__NVDLA_RENDER_NODE__=$NODE"

RUNTIME_STATUS=0
i=1
while [ "$i" -le "$repeat" ]; do
    if [ "$MODULE_STATUS" -eq 0 ] && [ "$DRI_STATUS" -eq 0 ] && [ -n "$NODE" ]; then
        cd /tmp
        rm -f output.dimg output-lenet.dimg output-lenet.txt
        NVDLA_DEVICE_NODE="$NODE" LD_LIBRARY_PATH=/mnt/r /mnt/r/nvdla_runtime \
            --loadable /mnt/r/loadable.nvdla \
            --image /mnt/r/seven.pgm \
            --rawdump >/tmp/runtime.$i.log 2>&1
        RUN_STATUS=$?
        if [ -f /tmp/output.dimg ]; then
            cp /tmp/output.dimg /tmp/output-lenet.$i.dimg
            cat /tmp/output.dimg >/tmp/output-lenet.$i.txt
            cp /tmp/output-lenet.$i.dimg /tmp/output-lenet.dimg
            cp /tmp/output-lenet.$i.txt /tmp/output-lenet.txt
        fi
    else
        echo "module_status=$MODULE_STATUS dri_status=$DRI_STATUS node=$NODE" >/tmp/runtime.$i.log
        RUN_STATUS=98
    fi

    if [ "$WRITE_STATUS" -eq 0 ]; then
        mkdir -p /mnt/w/runtime-output/repeat-$i
        cp /tmp/runtime.$i.log /mnt/w/runtime-output/repeat-$i/runtime.log 2>/dev/null
        cp /tmp/output-lenet.$i.dimg /mnt/w/runtime-output/repeat-$i/output.dimg 2>/dev/null
        cp /tmp/output-lenet.$i.txt /mnt/w/runtime-output/repeat-$i/output.txt 2>/dev/null
    fi

    echo "__NVDLA_STATUS_runtime_$i=$RUN_STATUS"
    if [ "$RUN_STATUS" -ne 0 ]; then
        RUNTIME_STATUS=1
        break
    fi
    i=$((i + 1))
done
if [ "$RUNTIME_STATUS" -eq 0 ] && { [ "$MODULE_STATUS" -ne 0 ] || [ "$DRI_STATUS" -ne 0 ] || [ -z "$NODE" ]; }; then
    RUNTIME_STATUS=98
fi

last_repeat="$i"
if [ "$last_repeat" -gt "$repeat" ]; then
    last_repeat="$repeat"
fi
cp "/tmp/runtime.$last_repeat.log" /tmp/runtime.log 2>/dev/null || : >/tmp/runtime.log
cat_section runtime /tmp/runtime.log
echo "__NVDLA_STATUS_runtime=$RUNTIME_STATUS"
cat_section output /tmp/output-lenet.txt

cat /proc/iomem >/tmp/iomem.txt 2>&1
cat /proc/meminfo >/tmp/meminfo.txt 2>&1
cat /proc/cmdline >/tmp/cmdline.txt 2>&1
dump_dt_summary
dmesg 2>&1 >/tmp/dmesg.log
tail -n 240 /tmp/dmesg.log >/tmp/dmesg-tail.log
cat_section dmesg /tmp/dmesg-tail.log

if [ "$WRITE_STATUS" -eq 0 ]; then
    cp /tmp/runtime.log /mnt/w/runtime-output/runtime.log 2>/dev/null
    cp /tmp/output-lenet.dimg /mnt/w/runtime-output/output.dimg 2>/dev/null
    cp /tmp/output-lenet.txt /mnt/w/runtime-output/output.txt 2>/dev/null
    cp /tmp/dmesg.log /mnt/w/dmesg.log 2>/dev/null
    cp /tmp/dmesg-tail.log /mnt/w/dmesg-tail.log 2>/dev/null
    cp /tmp/iomem.txt /mnt/w/iomem.txt 2>/dev/null
    cp /tmp/meminfo.txt /mnt/w/meminfo.txt 2>/dev/null
    cp /tmp/cmdline.txt /mnt/w/cmdline.txt 2>/dev/null
    cp /tmp/device-tree-summary.txt /mnt/w/device-tree-summary.txt 2>/dev/null
    cp /tmp/rcu-cpu-stall-timeout.txt /mnt/w/rcu-cpu-stall-timeout.txt 2>/dev/null
    cp /tmp/dev-dri.txt /mnt/w/dev-dri.txt 2>/dev/null
    cp /tmp/module-load.log /mnt/w/module-load.log 2>/dev/null
fi

echo "__NVDLA_RESULT__ module=$MODULE_STATUS dri=$DRI_STATUS runtime=$RUNTIME_STATUS repeat=$repeat"
echo "__NVDLA_RUNTIME_END__"

if [ "$MODULE_STATUS" -eq 0 ] && [ "$DRI_STATUS" -eq 0 ] && [ "$RUNTIME_STATUS" -eq 0 ]; then
    exit 0
fi
exit 1
EOF
chmod +x "$PAYLOAD/run-modern-smoke.sh"

sha256sum "$PAYLOAD"/* >"$OUT/input-sha256.txt"

set +e
if [[ "$VP_RUNNER" == "docker" ]]; then
    timeout -k 30 "$VP_TIMEOUT" "$DOCKER_BIN_RESOLVED" run --rm -i \
        -e SC_SIGNAL_WRITE_CHECK=DISABLE \
        "${TRACE_DOCKER_ENV[@]}" \
        -v "$OUT:/vp-run" \
        -v "$(dirname "$KERNEL_IMAGE"):/vp-kernel:ro" \
        -v "$(dirname "$ROOTFS_IMAGE"):/vp-rootfs:ro" \
        "${DOCKER_DTB_MOUNT[@]}" \
        -v "$PAYLOAD:/payload:ro" \
        -w /vp-run \
        "$DOCKER_IMAGE" \
        bash -lc "cd /vp-run && aarch64_toplevel -c /vp-run/modern-vp.lua" \
        | tee "$OUT/serial.log"
elif [[ "$VP_RUNNER" == "source-docker" ]]; then
    timeout -k 30 "$VP_TIMEOUT" "$DOCKER_BIN_RESOLVED" run --rm -i \
        -e SC_SIGNAL_WRITE_CHECK=DISABLE \
        "${TRACE_DOCKER_ENV[@]}" \
        -v "$OUT:/vp-run" \
        -v "$(dirname "$KERNEL_IMAGE"):/vp-kernel:ro" \
        -v "$(dirname "$ROOTFS_IMAGE"):/vp-rootfs:ro" \
        "${DOCKER_DTB_MOUNT[@]}" \
        -v "$PAYLOAD:/payload:ro" \
        -v "$VP_BINARY_DIR:/vp-small-bin:ro" \
        -v "$VP_LIBRARY_DIR:/vp-small-lib:ro" \
        -v "$VP_CMOD_LIBRARY_DIR:/vp-small-cmod:ro" \
        -w /vp-run \
        "$DOCKER_IMAGE" \
        bash -lc "export LD_LIBRARY_PATH=/vp-small-lib:/vp-small-cmod:/usr/local/systemc-2.3.0/lib-linux64:\${LD_LIBRARY_PATH:-}; cd /vp-run && /vp-small-bin/$VP_BINARY_BASENAME -c /vp-run/modern-vp.lua" \
        | tee "$OUT/serial.log"
else
    timeout -k 30 "$VP_TIMEOUT" env SC_SIGNAL_WRITE_CHECK=DISABLE LD_LIBRARY_PATH="$VP_LD_LIBRARY_PATH:${LD_LIBRARY_PATH:-}" \
        "${TRACE_HOST_ENV[@]}" \
        "$VP_BINARY" -c "$OUT/modern-vp.lua" \
        | tee "$OUT/serial.log"
fi
RUN_STATUS=${PIPESTATUS[0]}
set -e

OUTPUT_FILE="$OUT/runtime-output/output.txt"
OUTPUT_NORMALIZED=""
if [[ -f "$OUTPUT_FILE" ]]; then
    OUTPUT_NORMALIZED="$(tr -s '[:space:]' ' ' <"$OUTPUT_FILE" | sed 's/^ //; s/ $//')"
fi

TRACE_STATUS=0
REGISTER_HEADER=""
if [[ "$VP_TRACE" == "1" ]]; then
    REGISTER_HEADER="${NVDLA_REGISTER_HEADER:-$SOURCES_DIR/nvdla-sw/kmd/firmware/include/opendla_small.h}"
    if [[ ! -f "$REGISTER_HEADER" ]]; then
        REGISTER_HEADER="$ROOT/.work/nvdla-sw-patched/kmd/firmware/include/opendla_small.h"
    fi
    if [[ ! -f "$OUT/systemc.log" || ! -f "$REGISTER_HEADER" ]]; then
        TRACE_STATUS=1
    else
        set +e
        (cd /tmp && PYTHONPATH="$ROOT/tools:${PYTHONPATH:-}" "${PYTHON:-python3}" -m nvdla_test_framework trace-parse \
            --input "$OUT/systemc.log" \
            --register-header "$REGISTER_HEADER" \
            --csb-out "$OUT/csb-events.jsonl" \
            --raw-csb-out "$OUT/csb.raw.log" \
            --raw-dbb-out "$OUT/dbb.raw.log" \
            --summary-out "$OUT/trace-summary.json")
        TRACE_STATUS=$?
        set -e
    fi
fi

BAD_PATTERNS="Oops|BUG|WARNING|DMA-API|scheduler timeout|interrupt timeout|rcu_sched detected stalls|RCU grace-period|TLM_ADDRESS_ERROR_RESPONSE|invalid configuration"
BAD_PATTERN_INPUTS=()
for candidate in "$OUT/dmesg.log" "$OUT/serial.log"; do
    [[ -f "$candidate" ]] && BAD_PATTERN_INPUTS+=("$candidate")
done
if [[ "$VP_TRACE" == "1" && -f "$OUT/systemc.log" ]]; then
    BAD_PATTERN_INPUTS+=("$OUT/systemc.log")
fi
if [[ "${#BAD_PATTERN_INPUTS[@]}" -gt 0 ]]; then
    grep -E "$BAD_PATTERNS" "${BAD_PATTERN_INPUTS[@]}" >"$OUT/bad-patterns.log" || true
else
    : >"$OUT/bad-patterns.log"
fi

if [[ "$RUN_STATUS" -eq 0 && "$TRACE_STATUS" -eq 0 && "$OUTPUT_NORMALIZED" == "$EXPECTED_OUTPUT" && ! -s "$OUT/bad-patterns.log" ]]; then
    STATUS="pass"
else
    STATUS="fail"
fi

cat >"$OUT/manifest.json" <<EOF
{
  "schema_version": 1,
  "run_id": "$RUN_ID",
  "lane": "vp-modern",
  "mode": "$MODE_NAME",
  "status": "$STATUS",
  "vp_hw_config": "$VP_HW_CONFIG",
  "vp_runner": "$VP_RUNNER",
  "docker_status": $RUN_STATUS,
  "trace": {
    "enabled": $(if [[ "$VP_TRACE" == "1" ]]; then echo true; else echo false; fi),
    "status": $TRACE_STATUS,
    "verbosity": "$VP_TRACE_VERBOSITY",
    "systemc_sha256": "$(if [[ -f "$OUT/systemc.log" ]]; then hash_file "$OUT/systemc.log"; fi)",
    "canonical_csb_sha256": "$(if [[ -f "$OUT/csb-events.jsonl" ]]; then hash_file "$OUT/csb-events.jsonl"; fi)",
    "register_map_sha256": "$(if [[ -f "$REGISTER_HEADER" ]]; then hash_file "$REGISTER_HEADER"; fi)"
  },
  "repeat_count": $REPEAT,
  "expected_output": "$EXPECTED_OUTPUT",
  "actual_output": "$OUTPUT_NORMALIZED",
  "vp_ram": {
    "base": "$VP_RAM_BASE",
    "high": "$VP_RAM_HIGH"
  },
  "rcu_cpu_stall_timeout": "$RCU_CPU_STALL_TIMEOUT",
  "docker": {
    "binary": "$DOCKER_BIN_RESOLVED",
    "image": "$DOCKER_IMAGE",
    "image_id": "$DOCKER_IMAGE_ID"
  },
  "vp_binary": {
    "path": "$VP_BINARY",
    "sha256": "$(if [[ -f "$VP_BINARY" ]]; then hash_file "$VP_BINARY"; fi)",
    "ld_library_path": "$VP_LD_LIBRARY_PATH"
  },
  "vp_cmod": {
    "path": "$VP_CMOD_LIBRARY_DIR/libnvdla_cmod.so",
    "sha256": "$(if [[ -f "$VP_CMOD_LIBRARY_DIR/libnvdla_cmod.so" ]]; then hash_file "$VP_CMOD_LIBRARY_DIR/libnvdla_cmod.so"; fi)"
  },
  "patch_series_sha256": "$PATCH_SERIES_SHA",
  "inputs": {
    "kernel": {
      "path": "$KERNEL_IMAGE",
      "sha256": "$KERNEL_SHA"
    },
    "rootfs": {
      "path": "$ROOTFS_IMAGE",
      "sha256": "$ROOTFS_SHA"
    },
    "dtb": {
      "path": "$DTB_IMAGE",
      "sha256": "$DTB_SHA"
    },
    "module": {
      "path": "$KMOD",
      "sha256": "$KMOD_SHA",
      "vermagic": "$KMOD_VERMAGIC"
    },
    "runtime": {
      "path": "$RUNTIME_BIN",
      "sha256": "$RUNTIME_BIN_SHA"
    },
    "runtime_library": {
      "path": "$RUNTIME_LIB",
      "sha256": "$RUNTIME_LIB_SHA"
    },
    "loadable": {
      "path": "$LOADABLE",
      "sha256": "$LOADABLE_SHA",
      "config": "$LOADABLE_CONFIG"
    },
    "image": {
      "path": "$IMAGE",
      "sha256": "$IMAGE_SHA"
    }
  },
  "artifacts": {
    "serial": "serial.log",
    "dmesg": "dmesg.log",
    "runtime": "runtime-output/runtime.log",
    "output": "runtime-output/output.txt",
    "iomem": "iomem.txt",
    "meminfo": "meminfo.txt",
    "cmdline": "cmdline.txt",
    "device_tree_summary": "device-tree-summary.txt",
    "rcu_cpu_stall_timeout": "rcu-cpu-stall-timeout.txt",
    "bad_patterns": "bad-patterns.log"
    $(if [[ "$VP_TRACE" == "1" ]]; then printf ',\n    "systemc": "systemc.log",\n    "csb_raw": "csb.raw.log",\n    "dbb_raw": "dbb.raw.log",\n    "csb_events": "csb-events.jsonl",\n    "trace_summary": "trace-summary.json"'; fi)
  }
}
EOF

PYTHON_BIN="${PYTHON:-python3}"
set +e
(cd /tmp && PYTHONPATH="$ROOT/tools:${PYTHONPATH:-}" "$PYTHON_BIN" -m nvdla_test_framework lenet-analyze \
    --artifact "$OUT" \
    --expected-output "$EXPECTED_OUTPUT")
ANALYSIS_STATUS=$?
set -e
if [[ "$ANALYSIS_STATUS" -ne 0 || "$STATUS" != "pass" ]]; then
    STATUS="fail"
else
    STATUS="pass"
fi

if [[ "$VP_TRACE" == "1" ]]; then
    printf '%s\n' "$OUT" >"$ROOT/artifacts/latest-vp-trace-modern-small.txt"
fi

echo "VP modern LeNet $VP_HW_CONFIG status: $STATUS"
echo "Artifacts: $OUT"
if [[ "$STATUS" == "pass" ]]; then
    exit 0
fi
exit 1
