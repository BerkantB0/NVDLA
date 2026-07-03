#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="${WORK_DIR:-$HOME/build/nvdla-peta/vp-modern}"
LENET_DIR="${LENET_DIR:-$ROOT/artifacts/20260703T115149Z-vp-stock-lenet}"
DOCKER_IMAGE="${DOCKER_IMAGE:-nvdla/vp:latest}"
VP_TIMEOUT="${VP_TIMEOUT:-900}"

KERNEL_IMAGE="${VP_MODERN_KERNEL:-$WORK_DIR/kernel/arch/arm64/boot/Image.vp2m}"
ROOTFS_IMAGE="${VP_MODERN_ROOTFS:-$WORK_DIR/buildroot/images/rootfs-smoke.ext4}"
KMOD="${VP_MODERN_KO:-$WORK_DIR/modules/opendla.ko}"
RUNTIME_BIN="${VP_RUNTIME_BIN:-$WORK_DIR/runtime/nvdla_runtime}"
RUNTIME_LIB="${VP_RUNTIME_LIB:-$WORK_DIR/runtime/libnvdla_runtime.so}"

LOADABLE="$LENET_DIR/lenet_mnist.nv_full.nvdla"
IMAGE="$LENET_DIR/seven.pgm"
EXPECTED_OUTPUT="${EXPECTED_OUTPUT:-0 2 0 0 0 0 0 124 0 0}"

require_file() {
    local path="$1"
    if [[ ! -f "$path" ]]; then
        echo "missing required file: $path" >&2
        exit 2
    fi
}

require_file "$KERNEL_IMAGE"
require_file "$ROOTFS_IMAGE"
require_file "$KMOD"
require_file "$RUNTIME_BIN"
require_file "$RUNTIME_LIB"
require_file "$LOADABLE"
require_file "$IMAGE"

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
PATCH_SERIES_SHA="$(hash_patch_series)"
KMOD_VERMAGIC="$(modinfo -F vermagic "$KMOD" 2>/dev/null || true)"
DOCKER_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$DOCKER_IMAGE" 2>/dev/null || true)"

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-vp-modern-lenet-full"
OUT="$ROOT/artifacts/$RUN_ID"
PAYLOAD="$OUT/payload"
mkdir -p "$PAYLOAD"

cp "$KMOD" "$PAYLOAD/opendla.ko"
cp "$RUNTIME_BIN" "$PAYLOAD/nvdla_runtime"
cp "$RUNTIME_LIB" "$PAYLOAD/libnvdla_runtime.so"
cp "$LOADABLE" "$PAYLOAD/lenet_mnist.nv_full.nvdla"
cp "$IMAGE" "$PAYLOAD/seven.pgm"

cat >"$OUT/modern-vp.lua" <<EOF
CPU = {
    library = "libqbox-nvdla.so",
    extra_arguments = '-machine virt -cpu cortex-a57 -machine type=virt -nographic -smp 1 -m 1024 -kernel /vp-kernel/$(basename "$KERNEL_IMAGE") --append "root=/dev/vda" -drive file=/vp-rootfs/$(basename "$ROOTFS_IMAGE"),if=none,format=raw,id=hd0,snapshot=on -device virtio-blk-device,drive=hd0 -fsdev local,id=r,path=/payload,security_model=none -device virtio-9p-device,fsdev=r,mount_tag=r -fsdev local,id=w,path=/vp-run,security_model=none -device virtio-9p-device,fsdev=w,mount_tag=w -netdev user,id=user0,hostfwd=tcp::6666-:6666,hostfwd=tcp::6667-:22 -device virtio-net-device,netdev=user0'
}

ram = {
    size = 1048576,
    target_port = {
        base_addr = 0x40000000,
        high_addr = 0x7fffffff
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

echo "__NVDLA_RUNTIME_BEGIN__"

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

RUNTIME_STATUS=97
if [ "$MODULE_STATUS" -eq 0 ] && [ "$DRI_STATUS" -eq 0 ] && [ -n "$NODE" ]; then
    cd /tmp
    rm -f output.dimg output-lenet.dimg output-lenet.txt
    NVDLA_DEVICE_NODE="$NODE" LD_LIBRARY_PATH=/mnt/r /mnt/r/nvdla_runtime \
        --loadable /mnt/r/lenet_mnist.nv_full.nvdla \
        --image /mnt/r/seven.pgm \
        --rawdump >/tmp/runtime.log 2>&1
    RUNTIME_STATUS=$?
    if [ -f /tmp/output.dimg ]; then
        cp /tmp/output.dimg /tmp/output-lenet.dimg
        cat /tmp/output.dimg >/tmp/output-lenet.txt
    fi
else
    echo "module_status=$MODULE_STATUS dri_status=$DRI_STATUS node=$NODE" >/tmp/runtime.log
    RUNTIME_STATUS=98
fi

cat_section runtime /tmp/runtime.log
echo "__NVDLA_STATUS_runtime=$RUNTIME_STATUS"
cat_section output /tmp/output-lenet.txt

dmesg 2>&1 >/tmp/dmesg.log
tail -n 240 /tmp/dmesg.log >/tmp/dmesg-tail.log
cat_section dmesg /tmp/dmesg-tail.log

if [ "$WRITE_STATUS" -eq 0 ]; then
    cp /tmp/runtime.log /mnt/w/runtime-output/runtime.log 2>/dev/null
    cp /tmp/output-lenet.dimg /mnt/w/runtime-output/output.dimg 2>/dev/null
    cp /tmp/output-lenet.txt /mnt/w/runtime-output/output.txt 2>/dev/null
    cp /tmp/dmesg.log /mnt/w/dmesg.log 2>/dev/null
    cp /tmp/dmesg-tail.log /mnt/w/dmesg-tail.log 2>/dev/null
    cp /tmp/dev-dri.txt /mnt/w/dev-dri.txt 2>/dev/null
    cp /tmp/module-load.log /mnt/w/module-load.log 2>/dev/null
fi

echo "__NVDLA_RESULT__ module=$MODULE_STATUS dri=$DRI_STATUS runtime=$RUNTIME_STATUS"
echo "__NVDLA_RUNTIME_END__"

if [ "$MODULE_STATUS" -eq 0 ] && [ "$DRI_STATUS" -eq 0 ] && [ "$RUNTIME_STATUS" -eq 0 ]; then
    exit 0
fi
exit 1
EOF
chmod +x "$PAYLOAD/run-modern-smoke.sh"

sha256sum "$PAYLOAD"/* >"$OUT/input-sha256.txt"

set +e
timeout "$VP_TIMEOUT" docker run --rm -i \
    -e SC_SIGNAL_WRITE_CHECK=DISABLE \
    -v "$OUT:/vp-run" \
    -v "$(dirname "$KERNEL_IMAGE"):/vp-kernel:ro" \
    -v "$(dirname "$ROOTFS_IMAGE"):/vp-rootfs:ro" \
    -v "$PAYLOAD:/payload:ro" \
    -w /vp-run \
    "$DOCKER_IMAGE" \
    bash -lc "cd /vp-run && aarch64_toplevel -c /vp-run/modern-vp.lua" \
    | tee "$OUT/serial.log"
RUN_STATUS=${PIPESTATUS[0]}
set -e

OUTPUT_FILE="$OUT/runtime-output/output.txt"
OUTPUT_NORMALIZED=""
if [[ -f "$OUTPUT_FILE" ]]; then
    OUTPUT_NORMALIZED="$(tr -s '[:space:]' ' ' <"$OUTPUT_FILE" | sed 's/^ //; s/ $//')"
fi

BAD_PATTERNS="Oops|BUG|WARNING|DMA-API|scheduler timeout|interrupt timeout|rcu_sched detected stalls|RCU grace-period|TLM_ADDRESS_ERROR_RESPONSE|invalid configuration"
if [[ -f "$OUT/dmesg.log" ]]; then
    grep -E "$BAD_PATTERNS" "$OUT/dmesg.log" >"$OUT/bad-patterns.log" || true
else
    : >"$OUT/bad-patterns.log"
fi

if [[ "$RUN_STATUS" -eq 0 && "$OUTPUT_NORMALIZED" == "$EXPECTED_OUTPUT" && ! -s "$OUT/bad-patterns.log" ]]; then
    STATUS="pass"
else
    STATUS="fail"
fi

cat >"$OUT/manifest.json" <<EOF
{
  "schema_version": 1,
  "run_id": "$RUN_ID",
  "lane": "vp-modern",
  "mode": "lenet_full_control",
  "status": "$STATUS",
  "docker_status": $RUN_STATUS,
  "expected_output": "$EXPECTED_OUTPUT",
  "actual_output": "$OUTPUT_NORMALIZED",
  "docker": {
    "image": "$DOCKER_IMAGE",
    "image_id": "$DOCKER_IMAGE_ID"
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
      "config": "nv_full"
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
    "bad_patterns": "bad-patterns.log"
  }
}
EOF

echo "VP modern LeNet full status: $STATUS"
echo "Artifacts: $OUT"
if [[ "$STATUS" == "pass" ]]; then
    exit 0
fi
exit 1
