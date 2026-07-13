#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/petalinux_common.sh"

cd "$ROOT"
pl_start_run "runtime"

if ! pl_source_settings; then
  pl_finish_fail "failed to source PetaLinux settings"
fi
pl_require_project

DEST="$PETALINUX_PROJECT/project-spec/meta-user/recipes-apps/nvdla-runtime"
rm -rf "$DEST"
mkdir -p "$DEST"
cp -r "$ROOT/recipes/petalinux/apps/nvdla-runtime/"* "$DEST/"

PATCH_INC="$DEST/nvdla-runtime-patches.inc"
pl_install_patch_queue "$DEST" "$PATCH_INC" "scripts/petalinux_runtime.sh"

IMAGE_DEST="$PETALINUX_PROJECT/project-spec/meta-user/recipes-core/images"
mkdir -p "$IMAGE_DEST"
rm -f "$IMAGE_DEST/petalinux-image-minimal_%.bbappend"
IMAGE_APPEND_PATH="$IMAGE_DEST/petalinux-image-minimal.bbappend"
cp "$ROOT/recipes/petalinux/images/nvdla-stack/petalinux-image-minimal.bbappend" "$IMAGE_APPEND_PATH"

RECIPE_FILES="$(find "$DEST" -maxdepth 2 -type f -printf '%P\n' | sort | paste -sd ':' -):recipes-core/images/$(basename "$IMAGE_APPEND_PATH")"
RUNTIME_RECIPE_PATH="$DEST/nvdla-runtime.bb"
export RECIPE_FILES RUNTIME_RECIPE_PATH IMAGE_APPEND_PATH

{
  echo "Installed nvdla-runtime recipe into $DEST"
  echo "Installed NVDLA image append into $IMAGE_APPEND_PATH"
  echo "Building nvdla-runtime in $PETALINUX_PROJECT"
} | tee "$BUILD_LOG"

petalinux-build -p "$PETALINUX_PROJECT" -c nvdla-runtime 2>&1 | tee -a "$BUILD_LOG" \
  || pl_finish_fail "petalinux-build -c nvdla-runtime failed"

qa_pattern='QA Issue:.*\[(rpaths|textrel|file-rdeps|already-stripped|buildpaths)\]'
if grep -E "$qa_pattern" "$BUILD_LOG" >"$RUN_DIR/runtime-qa-errors.log"; then
  pl_finish_fail "nvdla-runtime produced a forbidden Yocto QA finding"
fi
rm -f "$RUN_DIR/runtime-qa-errors.log"

RUNTIME_BINARY_PATH="$(
  find "$PETALINUX_PROJECT/build/tmp/deploy/images" -type f -name nvdla_runtime -printf '%T@ %p\n' 2>/dev/null \
    | sort -n | tail -n 1 | cut -d ' ' -f 2-
)"
RUNTIME_LIBRARY_PATH="$(
  find "$PETALINUX_PROJECT/build/tmp/deploy/images" -type f -name libnvdla_runtime.so -printf '%T@ %p\n' 2>/dev/null \
    | sort -n | tail -n 1 | cut -d ' ' -f 2-
)"
RUNTIME_PACKAGE_PATH="$(
  find "$PETALINUX_PROJECT/build/tmp/deploy/rpm" -type f -name 'nvdla-runtime-[0-9]*.rpm' -printf '%T@ %p\n' 2>/dev/null \
    | sort -n | tail -n 1 | cut -d ' ' -f 2-
)"
export RUNTIME_BINARY_PATH RUNTIME_LIBRARY_PATH RUNTIME_PACKAGE_PATH

if [[ -z "$RUNTIME_BINARY_PATH" || ! -f "$RUNTIME_BINARY_PATH" ]]; then
  pl_finish_fail "nvdla_runtime was not deployed"
fi
if [[ -z "$RUNTIME_LIBRARY_PATH" || ! -f "$RUNTIME_LIBRARY_PATH" ]]; then
  pl_finish_fail "libnvdla_runtime.so was not deployed"
fi
if [[ -z "$RUNTIME_PACKAGE_PATH" || ! -f "$RUNTIME_PACKAGE_PATH" ]]; then
  pl_finish_fail "nvdla-runtime RPM was not produced"
fi

{
  echo "PetaLinux runtime build passed"
  echo "  binary: $RUNTIME_BINARY_PATH"
  echo "  library: $RUNTIME_LIBRARY_PATH"
  echo "  package: $RUNTIME_PACKAGE_PATH"
  sha256sum "$RUNTIME_BINARY_PATH" "$RUNTIME_LIBRARY_PATH" "$RUNTIME_PACKAGE_PATH"
} | tee -a "$BUILD_LOG"

pl_write_manifest "pass"
