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

image_append="$PETALINUX_PROJECT/project-spec/meta-user/recipes-core/images/petalinux-image-minimal.bbappend"
if [[ ! -f "$image_append" ]]; then
  pl_finish_blocked "NVDLA image package append is missing; run make petalinux-runtime first"
fi
if ! grep -Eq 'IMAGE_INSTALL:append.*opendla.*nvdla-runtime.*nvdla-board-tools' "$image_append"; then
  pl_finish_fail "NVDLA image package append does not include the complete board bring-up stack"
fi
export IMAGE_APPEND_PATH="$image_append"

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
