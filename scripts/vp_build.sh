#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ACTION="${1:-}"
CROSS_COMPILE="${CROSS_COMPILE:-aarch64-linux-gnu-}"
ARCH="${ARCH:-arm64}"
WORK="${WORK_DIR:-$ROOT/.work/vp-modern}"
SOURCES="$ROOT/.external/sources"
LINUX="$SOURCES/linux-xlnx"
BUILDROOT="$SOURCES/buildroot"
NVDLA_SW="$SOURCES/nvdla-sw"

usage() {
  echo "Usage: $0 {kernel|rootfs|kmod|all}" >&2
}

need_dir() {
  local dir="$1"
  local hint="$2"
  if [[ ! -d "$dir" ]]; then
    echo "ERROR: missing $dir" >&2
    echo "       $hint" >&2
    exit 2
  fi
}

build_kernel() {
  need_dir "$LINUX" "Run: make sources-heavy"
  mkdir -p "$WORK/kernel"
  echo "Building VP kernel from $LINUX"
  make -C "$LINUX" O="$WORK/kernel" ARCH="$ARCH" CROSS_COMPILE="$CROSS_COMPILE" defconfig
  "$LINUX/scripts/config" --file "$WORK/kernel/.config" \
    --enable CONFIG_DRM \
    --enable CONFIG_DMA_SHARED_BUFFER \
    --enable CONFIG_CMA \
    --enable CONFIG_DMA_CMA \
    --enable CONFIG_MODULES \
    --enable CONFIG_DEVTMPFS \
    --enable CONFIG_DEVTMPFS_MOUNT \
    --enable CONFIG_VIRTIO \
    --enable CONFIG_VIRTIO_BLK \
    --enable CONFIG_NET_9P \
    --enable CONFIG_9P_FS
  make -C "$LINUX" O="$WORK/kernel" ARCH="$ARCH" CROSS_COMPILE="$CROSS_COMPILE" olddefconfig
  make -C "$LINUX" O="$WORK/kernel" ARCH="$ARCH" CROSS_COMPILE="$CROSS_COMPILE" -j"$(nproc)" Image modules dtbs
}

build_rootfs() {
  need_dir "$BUILDROOT" "Run: make sources-heavy"
  mkdir -p "$WORK/buildroot"
  echo "Building VP rootfs from $BUILDROOT"
  make -C "$BUILDROOT" O="$WORK/buildroot" BR2_EXTERNAL="$ROOT/configs/vp/buildroot_external" nvdla_vp_modern_defconfig
  make -C "$BUILDROOT" O="$WORK/buildroot" -j"$(nproc)"
}

build_kmod() {
  need_dir "$NVDLA_SW" "Run: make sources"
  if [[ ! -d "$WORK/kernel" ]]; then
    echo "ERROR: VP kernel build dir not found at $WORK/kernel" >&2
    echo "       Run: make vp-kernel" >&2
    exit 2
  fi
  local kmd="$NVDLA_SW/kmd/port/linux"
  if [[ ! -d "$kmd" ]]; then
    echo "ERROR: NVDLA KMD path not found: $kmd" >&2
    exit 2
  fi
  echo "Building opendla.ko against $WORK/kernel"
  make -C "$kmd" KDIR="$WORK/kernel" ARCH="$ARCH" CROSS_COMPILE="$CROSS_COMPILE"
  mkdir -p "$WORK/modules"
  cp "$kmd/opendla.ko" "$WORK/modules/opendla.ko"
  sha256sum "$WORK/modules/opendla.ko"
}

case "$ACTION" in
  kernel) build_kernel ;;
  rootfs) build_rootfs ;;
  kmod) build_kmod ;;
  all) build_kernel; build_rootfs; build_kmod ;;
  *) usage; exit 2 ;;
esac

