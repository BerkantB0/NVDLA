#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/petalinux_common.sh"

cd "$ROOT"
pl_start_run "board-tools"

if ! pl_source_settings; then
  pl_finish_fail "failed to source PetaLinux settings"
fi
pl_require_project

DEST="$PETALINUX_PROJECT/project-spec/meta-user/recipes-apps/nvdla-board-tools"
rm -rf "$DEST"
mkdir -p "$DEST/files"
cp -r "$ROOT/recipes/petalinux/apps/nvdla-board-tools/"* "$DEST/"
cp "$ROOT/tools/smoke/nvdla-kmd-smoke.c" "$DEST/files/nvdla-kmd-smoke.c"
cp "$ROOT/tools/runtime/nvdla-flatbuf-client.c" "$DEST/files/nvdla-flatbuf-client.c"
cp "$ROOT/tools/board/nvdla-board-check" "$DEST/files/nvdla-board-check"
cp "$ROOT/tools/board/nvdla-board-workload" "$DEST/files/nvdla-board-workload"
cp "$ROOT/tools/board/serial-root-autologin.conf" "$DEST/files/serial-root-autologin.conf"
cp "$ROOT/tools/board/20-nvdla-direct.network" "$DEST/files/20-nvdla-direct.network"

PATCH_INC="$DEST/nvdla-board-tools-patches.inc"
pl_install_patch_queue "$DEST" "$PATCH_INC" "scripts/petalinux_board_tools.sh"

IMAGE_DEST="$PETALINUX_PROJECT/project-spec/meta-user/recipes-core/images"
mkdir -p "$IMAGE_DEST"
IMAGE_APPEND_PATH="$IMAGE_DEST/petalinux-image-minimal.bbappend"
cp "$ROOT/recipes/petalinux/images/nvdla-stack/petalinux-image-minimal.bbappend" "$IMAGE_APPEND_PATH"

RECIPE_FILES="$(find "$DEST" -maxdepth 2 -type f -printf '%P\n' | sort | paste -sd ':' -):recipes-core/images/$(basename "$IMAGE_APPEND_PATH")"
BOARD_TOOLS_RECIPE_PATH="$DEST/nvdla-board-tools.bb"
export RECIPE_FILES BOARD_TOOLS_RECIPE_PATH IMAGE_APPEND_PATH

{
  echo "Installed nvdla-board-tools recipe into $DEST"
  echo "Installed NVDLA bring-up image append into $IMAGE_APPEND_PATH"
  echo "Building nvdla-board-tools in $PETALINUX_PROJECT"
} | tee "$BUILD_LOG"

petalinux-build -p "$PETALINUX_PROJECT" -c nvdla-board-tools 2>&1 | tee -a "$BUILD_LOG" \
  || pl_finish_fail "petalinux-build -c nvdla-board-tools failed"

qa_pattern='QA Issue:.*\[(rpaths|textrel|file-rdeps|already-stripped|buildpaths)\]'
if grep -E "$qa_pattern" "$BUILD_LOG" >"$RUN_DIR/board-tools-qa-errors.log"; then
  pl_finish_fail "nvdla-board-tools produced a forbidden Yocto QA finding"
fi
rm -f "$RUN_DIR/board-tools-qa-errors.log"

BOARD_SMOKE_BINARY_PATH="$(
  find "$PETALINUX_PROJECT/build/tmp/deploy/images" -type f -name nvdla-kmd-smoke -printf '%T@ %p\n' 2>/dev/null \
    | sort -n | tail -n 1 | cut -d ' ' -f 2-
)"
BOARD_CHECK_SCRIPT_PATH="$(
  find "$PETALINUX_PROJECT/build/tmp/deploy/images" -type f -name nvdla-board-check -printf '%T@ %p\n' 2>/dev/null \
    | sort -n | tail -n 1 | cut -d ' ' -f 2-
)"
BOARD_FLATBUF_CLIENT_PATH="$(
  find "$PETALINUX_PROJECT/build/tmp/deploy/images" -type f -name nvdla-flatbuf-client -printf '%T@ %p\n' 2>/dev/null \
    | sort -n | tail -n 1 | cut -d ' ' -f 2-
)"
BOARD_WORKLOAD_SCRIPT_PATH="$(
  find "$PETALINUX_PROJECT/build/tmp/deploy/images" -type f -name nvdla-board-workload -printf '%T@ %p\n' 2>/dev/null \
    | sort -n | tail -n 1 | cut -d ' ' -f 2-
)"
BOARD_TOOLS_PACKAGE_PATH="$(
  find "$PETALINUX_PROJECT/build/tmp/deploy/rpm" -type f -name 'nvdla-board-tools-[0-9]*.rpm' -printf '%T@ %p\n' 2>/dev/null \
    | sort -n | tail -n 1 | cut -d ' ' -f 2-
)"
export BOARD_SMOKE_BINARY_PATH BOARD_FLATBUF_CLIENT_PATH BOARD_CHECK_SCRIPT_PATH
export BOARD_WORKLOAD_SCRIPT_PATH BOARD_TOOLS_PACKAGE_PATH

if [[ -z "$BOARD_SMOKE_BINARY_PATH" || ! -f "$BOARD_SMOKE_BINARY_PATH" ]]; then
  pl_finish_fail "nvdla-kmd-smoke was not deployed"
fi
if [[ -z "$BOARD_CHECK_SCRIPT_PATH" || ! -f "$BOARD_CHECK_SCRIPT_PATH" ]]; then
  pl_finish_fail "nvdla-board-check was not deployed"
fi
if [[ -z "$BOARD_FLATBUF_CLIENT_PATH" || ! -f "$BOARD_FLATBUF_CLIENT_PATH" ]]; then
  pl_finish_fail "nvdla-flatbuf-client was not deployed"
fi
if [[ -z "$BOARD_WORKLOAD_SCRIPT_PATH" || ! -f "$BOARD_WORKLOAD_SCRIPT_PATH" ]]; then
  pl_finish_fail "nvdla-board-workload was not deployed"
fi
if [[ -z "$BOARD_TOOLS_PACKAGE_PATH" || ! -f "$BOARD_TOOLS_PACKAGE_PATH" ]]; then
  pl_finish_fail "nvdla-board-tools RPM was not produced"
fi

{
  echo "PetaLinux board-tools build passed"
  echo "  smoke: $BOARD_SMOKE_BINARY_PATH"
  echo "  flatbuffer client: $BOARD_FLATBUF_CLIENT_PATH"
  echo "  collector: $BOARD_CHECK_SCRIPT_PATH"
  echo "  workload runner: $BOARD_WORKLOAD_SCRIPT_PATH"
  echo "  package: $BOARD_TOOLS_PACKAGE_PATH"
  sha256sum "$BOARD_SMOKE_BINARY_PATH" "$BOARD_FLATBUF_CLIENT_PATH" \
    "$BOARD_CHECK_SCRIPT_PATH" "$BOARD_WORKLOAD_SCRIPT_PATH" "$BOARD_TOOLS_PACKAGE_PATH"
} | tee -a "$BUILD_LOG"

pl_write_manifest "pass"
