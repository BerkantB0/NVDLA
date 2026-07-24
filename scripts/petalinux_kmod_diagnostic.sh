#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/petalinux_common.sh"

cd "$ROOT"
pl_start_run "kmod-diagnostic"

export NVDLA_KMD_CONFIG=small
export NVDLA_HW_CONFIG=small
export NVDLA_KMD_DIAGNOSTIC=1

if ! pl_source_settings; then
  pl_finish_fail "failed to source PetaLinux settings"
fi
pl_require_project

DEST="$PETALINUX_PROJECT/project-spec/meta-user/recipes-modules/opendla-diagnostic"
rm -rf "$DEST"
mkdir -p "$DEST"
cp -r "$ROOT/recipes/petalinux/modules/opendla-diagnostic/"* "$DEST/"

PATCH_INC="$DEST/opendla-diagnostic-patches.inc"
pl_install_patch_queue "$DEST" "$PATCH_INC" "scripts/petalinux_kmod_diagnostic.sh"
for patch in "$ROOT"/patches/debug/nvdla-sw/*.patch; do
  cp "$patch" "$DEST/files/"
  printf 'SRC_URI += "file://%s"\n' "$(basename "$patch")" >>"$PATCH_INC"
done

PATCH_SERIES_SHA="$(
  find patches/nvdla-sw patches/debug/nvdla-sw -type f -name '*.patch' -print0 \
    | sort -z \
    | xargs -0 sha256sum \
    | sha256sum \
    | cut -d ' ' -f 1
)"
export PATCH_SERIES_SHA

{
  echo "Installed failure-only opendla diagnostic recipe into $DEST"
  echo "Building opendla-diagnostic in $PETALINUX_PROJECT"
  echo "NVDLA_HW_CONFIG=small"
  echo "NVDLA_KMD_TRACE=1"
} | tee "$BUILD_LOG"

petalinux-build -p "$PETALINUX_PROJECT" -c opendla-diagnostic 2>&1 | tee -a "$BUILD_LOG" \
  || pl_finish_fail "petalinux-build -c opendla-diagnostic failed"

DEPLOYED_MODULE="$(
  find "$PETALINUX_PROJECT/build/tmp/deploy" -type f -name opendla-diagnostic.ko \
    -printf '%T@ %p\n' 2>/dev/null \
    | sort -n \
    | tail -n 1 \
    | cut -d ' ' -f 2-
)"
if [[ -z "$DEPLOYED_MODULE" ]]; then
  pl_finish_fail "diagnostic build completed but opendla-diagnostic.ko was not deployed"
fi

MODULE_PATH="$RUN_DIR/opendla-diagnostic.ko"
cp "$DEPLOYED_MODULE" "$MODULE_PATH"
MODULE_VERMAGIC="$(pl_module_vermagic "$MODULE_PATH")"
export MODULE_PATH MODULE_VERMAGIC

{
  echo "Failure-only diagnostic KMD build passed: $MODULE_PATH"
  echo "module vermagic: ${MODULE_VERMAGIC:-unknown}"
  echo "This module is not installed in the production rootfs."
} | tee -a "$BUILD_LOG"
pl_write_manifest "pass"
