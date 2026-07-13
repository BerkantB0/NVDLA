#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/petalinux_common.sh"

cd "$ROOT"
pl_start_run "kmod"

KMD_CONFIG="${NVDLA_KMD_CONFIG:-${NVDLA_HW_CONFIG:-small}}"
export NVDLA_KMD_CONFIG="$KMD_CONFIG"
export NVDLA_HW_CONFIG="$KMD_CONFIG"

if [[ "$KMD_CONFIG" != "small" && "$KMD_CONFIG" != "initial" ]]; then
  pl_finish_fail "unsupported NVDLA_KMD_CONFIG=$KMD_CONFIG; expected small or initial"
fi
if ! pl_source_settings; then
  pl_finish_fail "failed to source PetaLinux settings"
fi
pl_require_project

DEST="$PETALINUX_PROJECT/project-spec/meta-user/recipes-modules/opendla"
rm -rf "$DEST"
mkdir -p "$DEST"
cp -r "$ROOT/recipes/petalinux/modules/opendla/"* "$DEST/"

PATCH_INC="$DEST/opendla-patches.inc"
pl_install_patch_queue "$DEST" "$PATCH_INC" "scripts/petalinux_kmod.sh"
printf 'NVDLA_HW_CONFIG = "%s"\n' "$KMD_CONFIG" >>"$PATCH_INC"

RECIPE_FILES="$(find "$DEST" -maxdepth 2 -type f -printf '%P\n' | sort | paste -sd ':' -)"
export RECIPE_FILES

{
  echo "Installed opendla recipe skeleton into $DEST"
  echo "Building opendla in $PETALINUX_PROJECT"
  echo "NVDLA_HW_CONFIG=$KMD_CONFIG"
} | tee "$BUILD_LOG"

petalinux-build -p "$PETALINUX_PROJECT" -c opendla 2>&1 | tee -a "$BUILD_LOG" \
  || pl_finish_fail "petalinux-build -c opendla failed"

MODULE_PATH="$(
  find "$PETALINUX_PROJECT" -type f -name opendla.ko -printf '%T@ %p\n' 2>/dev/null \
    | sort -n \
    | tail -n 1 \
    | cut -d ' ' -f 2-
)"
if [[ -z "$MODULE_PATH" ]]; then
  pl_finish_fail "petalinux-build completed but opendla.ko was not found"
fi
MODULE_VERMAGIC="$(pl_module_vermagic "$MODULE_PATH")"
export MODULE_PATH MODULE_VERMAGIC

{
  echo "PetaLinux KMD build passed: $MODULE_PATH"
  echo "module vermagic: ${MODULE_VERMAGIC:-unknown}"
} | tee -a "$BUILD_LOG"
pl_write_manifest "pass"
