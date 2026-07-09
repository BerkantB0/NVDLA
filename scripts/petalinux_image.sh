#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/petalinux_common.sh"

cd "$ROOT"
pl_start_run "image"

if ! pl_source_settings; then
  pl_finish_fail "failed to source PetaLinux settings"
fi
pl_require_project

{
  echo "Building PetaLinux image"
  echo "  project: $PETALINUX_PROJECT"
} | tee "$BUILD_LOG"

petalinux-build -p "$PETALINUX_PROJECT" 2>&1 | tee -a "$BUILD_LOG" \
  || pl_finish_fail "petalinux-build failed"

image_ub="$PETALINUX_PROJECT/images/linux/image.ub"
system_dtb="$PETALINUX_PROJECT/images/linux/system.dtb"
if [[ ! -f "$image_ub" ]]; then
  pl_finish_fail "petalinux-build completed but image.ub was not found"
fi
if [[ ! -f "$system_dtb" ]]; then
  pl_finish_fail "petalinux-build completed but system.dtb was not found"
fi

echo "PetaLinux image build passed: $image_ub" | tee -a "$BUILD_LOG"
pl_write_manifest "pass"
