#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/petalinux_common.sh"

cd "$ROOT"
pl_start_run "rootfs-audit"

if ! pl_source_settings; then
  pl_finish_fail "failed to source PetaLinux settings"
fi
pl_require_project

ROOTFS_TAR_PATH="${PETALINUX_ROOTFS_TAR:-$PETALINUX_PROJECT/images/linux/rootfs.tar.gz}"
ROOTFS_AUDIT_PATH="$RUN_DIR/rootfs-audit.json"
IMAGE_APPEND_PATH="$PETALINUX_PROJECT/project-spec/meta-user/recipes-core/images/petalinux-image-minimal.bbappend"
RUNTIME_RECIPE_PATH="$PETALINUX_PROJECT/project-spec/meta-user/recipes-apps/nvdla-runtime/nvdla-runtime.bb"
RUNTIME_BINARY_PATH="$RUN_DIR/rootfs-files/usr/bin/nvdla_runtime"
RUNTIME_LIBRARY_PATH="$RUN_DIR/rootfs-files/usr/lib/libnvdla_runtime.so"
RUNTIME_PACKAGE_PATH="$(
  { find "$PETALINUX_PROJECT/build/tmp/deploy/rpm" -type f -name 'nvdla-runtime-[0-9]*.rpm' -printf '%T@ %p\n' 2>/dev/null || true; } \
    | sort -n | tail -n 1 | cut -d ' ' -f 2-
)"
export ROOTFS_TAR_PATH ROOTFS_AUDIT_PATH IMAGE_APPEND_PATH RUNTIME_RECIPE_PATH
export RUNTIME_BINARY_PATH RUNTIME_LIBRARY_PATH RUNTIME_PACKAGE_PATH

if [[ ! -f "$ROOTFS_TAR_PATH" ]]; then
  pl_finish_blocked "PetaLinux rootfs archive is missing; run make petalinux-image first"
fi
if [[ -z "$RUNTIME_PACKAGE_PATH" || ! -f "$RUNTIME_PACKAGE_PATH" ]]; then
  pl_finish_fail "nvdla-runtime RPM is missing; run make petalinux-runtime first"
fi

if ! python3 -m nvdla_test_framework petalinux-rootfs-audit \
  --rootfs "$ROOTFS_TAR_PATH" \
  --extract-dir "$RUN_DIR/rootfs-files" \
  --out "$ROOTFS_AUDIT_PATH" 2>&1 | tee "$BUILD_LOG"; then
  pl_finish_fail "PetaLinux rootfs audit failed"
fi

MODULE_PATH="$(find "$RUN_DIR/rootfs-files/lib/modules" -type f -name opendla.ko | sort | head -n 1)"
if [[ -z "$MODULE_PATH" ]]; then
  pl_finish_fail "rootfs audit passed without an extracted opendla.ko"
fi
MODULE_VERMAGIC="$(pl_module_vermagic "$MODULE_PATH")"
export MODULE_PATH MODULE_VERMAGIC

echo "PetaLinux rootfs audit passed: $ROOTFS_TAR_PATH" | tee -a "$BUILD_LOG"
pl_write_manifest "pass"
