#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/petalinux_common.sh"

cd "$ROOT"
pl_start_run "sd-bundle"

if ! pl_source_settings; then
  pl_finish_fail "failed to source PetaLinux settings"
fi
pl_require_project

BOOT_BIN_PATH="${PETALINUX_BOOT_BIN:-$PETALINUX_PROJECT/images/linux/BOOT.BIN}"
BOOT_SCRIPT_PATH="${PETALINUX_BOOT_SCRIPT:-$PETALINUX_PROJECT/images/linux/boot.scr}"
FIT_IMAGE_PATH="${PETALINUX_FIT_IMAGE:-$PETALINUX_PROJECT/images/linux/image.ub}"
SD_CARD_DIR="$RUN_DIR/sd-card"
SD_BUNDLE_PATH="$RUN_DIR/nvdla-zcu102-sd.tar.gz"
SD_BUNDLE_MANIFEST_PATH="$RUN_DIR/sd-bundle.json"
export PACKAGE_PATH="$BOOT_BIN_PATH"
export SD_BUNDLE_PATH SD_BUNDLE_MANIFEST_PATH

if ! python3 -m nvdla_test_framework petalinux-sd-bundle \
  --boot-bin "$BOOT_BIN_PATH" \
  --boot-script "$BOOT_SCRIPT_PATH" \
  --fit-image "$FIT_IMAGE_PATH" \
  --out-dir "$SD_CARD_DIR" \
  --archive "$SD_BUNDLE_PATH" \
  --manifest "$SD_BUNDLE_MANIFEST_PATH" 2>&1 | tee "$BUILD_LOG"; then
  pl_finish_fail "PetaLinux SD bundle generation failed"
fi

{
  echo "PetaLinux SD handoff passed"
  echo "  copy directory: $SD_CARD_DIR"
  echo "  archive: $SD_BUNDLE_PATH"
  sha256sum "$SD_CARD_DIR/BOOT.BIN" "$SD_CARD_DIR/boot.scr" "$SD_CARD_DIR/image.ub" "$SD_BUNDLE_PATH"
} | tee -a "$BUILD_LOG"

pl_write_manifest "pass"
