#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/petalinux_common.sh"

cd "$ROOT"
pl_start_run "package"

if ! pl_source_settings; then
  pl_finish_fail "failed to source PetaLinux settings"
fi
pl_require_project

image_ub="$PETALINUX_PROJECT/images/linux/image.ub"
if [[ ! -f "$image_ub" ]]; then
  pl_finish_blocked "image.ub is missing; run make petalinux-image first"
fi

package_out="$RUN_DIR/BOOT.BIN"
rm -f "$package_out"

{
  echo "Packaging PetaLinux boot image"
  echo "  project: $PETALINUX_PROJECT"
  echo "  output: $package_out"
} | tee "$BUILD_LOG"

(
  cd "$RUN_DIR"
  petalinux-package boot -p "$PETALINUX_PROJECT" --u-boot --fpga --output "$package_out"
) 2>&1 | tee -a "$BUILD_LOG" || pl_finish_fail "petalinux-package boot failed"

if [[ ! -f "$package_out" ]]; then
  pl_finish_fail "petalinux-package completed but BOOT.BIN was not found"
fi

install -m 0644 "$package_out" "$PETALINUX_PROJECT/images/linux/BOOT.BIN"
export PACKAGE_PATH="$package_out"
echo "PetaLinux package passed: $package_out" | tee -a "$BUILD_LOG"
pl_write_manifest "pass"
