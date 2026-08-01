#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/petalinux_common.sh"

cd "$ROOT"
pl_start_run "cpu-runtime"

if ! pl_source_settings; then
  pl_finish_fail "failed to source PetaLinux settings"
fi
pl_require_project

CPU_RUNTIME_WORK_DIR="${CPU_RUNTIME_WORK_DIR:-$HOME/build/nvdla-peta/cpu-onnxruntime}"
SOURCES_DIR="${SOURCES_DIR:-$ROOT/.external/sources}"
BUILD_OUTPUT="$CPU_RUNTIME_WORK_DIR/install"
ONNXRUNTIME_SOURCE="$SOURCES_DIR/onnxruntime"
for input in \
  "$BUILD_OUTPUT/libonnxruntime.so.1.18.1" \
  "$BUILD_OUTPUT/onnx_test_runner" \
  "$BUILD_OUTPUT/onnxruntime_perf_test" \
  "$BUILD_OUTPUT/build-evidence.txt" \
  "$ONNXRUNTIME_SOURCE/LICENSE"; do
  if [[ ! -f "$input" ]]; then
    pl_finish_blocked "missing ONNX Runtime build input: $input; run make cpu-onnxruntime"
  fi
done

expected_commit="$(python3 -c 'import json; print(json.load(open("repro.lock.json"))["sources"]["onnxruntime"]["commit"])')"
actual_commit="$(git -C "$ONNXRUNTIME_SOURCE" rev-parse HEAD)"
if [[ "$actual_commit" != "$expected_commit" ]]; then
  pl_finish_fail "ONNX Runtime source mismatch: expected $expected_commit got $actual_commit"
fi

DEST="$PETALINUX_PROJECT/project-spec/meta-user/recipes-apps/onnxruntime-cpu-tools"
rm -rf "$DEST"
mkdir -p "$DEST/files"
cp "$ROOT/recipes/petalinux/apps/onnxruntime-cpu-tools/onnxruntime-cpu-tools.bb" "$DEST/"
cp "$ONNXRUNTIME_SOURCE/LICENSE" "$DEST/files/LICENSE"
cp "$BUILD_OUTPUT/libonnxruntime.so.1.18.1" "$DEST/files/"
cp "$BUILD_OUTPUT/onnx_test_runner" "$BUILD_OUTPUT/onnxruntime_perf_test" "$DEST/files/"

IMAGE_DEST="$PETALINUX_PROJECT/project-spec/meta-user/recipes-core/images"
mkdir -p "$IMAGE_DEST"
IMAGE_APPEND_PATH="$IMAGE_DEST/petalinux-image-minimal.bbappend"
cp "$ROOT/recipes/petalinux/images/nvdla-stack/petalinux-image-minimal.bbappend" "$IMAGE_APPEND_PATH"

CPU_RUNTIME_RECIPE_PATH="$DEST/onnxruntime-cpu-tools.bb"
RECIPE_FILES="$(find "$DEST" -maxdepth 2 -type f -printf '%P\n' | sort | paste -sd ':' -):recipes-core/images/$(basename "$IMAGE_APPEND_PATH")"
export RECIPE_FILES IMAGE_APPEND_PATH CPU_RUNTIME_RECIPE_PATH
export CPU_RUNTIME_SOURCE_COMMIT="$actual_commit"

{
  echo "Installed onnxruntime-cpu-tools recipe into $DEST"
  echo "Installed CPU runtime image append into $IMAGE_APPEND_PATH"
  echo "Building onnxruntime-cpu-tools in $PETALINUX_PROJECT"
} | tee "$BUILD_LOG"

petalinux-build -p "$PETALINUX_PROJECT" -c onnxruntime-cpu-tools 2>&1 | tee -a "$BUILD_LOG" \
  || pl_finish_fail "petalinux-build -c onnxruntime-cpu-tools failed"

qa_pattern='QA Issue:.*\[(rpaths|textrel|file-rdeps|already-stripped|buildpaths)\]'
if grep -E "$qa_pattern" "$BUILD_LOG" >"$RUN_DIR/cpu-runtime-qa-errors.log"; then
  pl_finish_fail "onnxruntime-cpu-tools produced a forbidden Yocto QA finding"
fi
rm -f "$RUN_DIR/cpu-runtime-qa-errors.log"

latest_deploy() {
  local name="$1"
  find "$PETALINUX_PROJECT/build/tmp/deploy/images" -type f -name "$name" -printf '%T@ %p\n' 2>/dev/null \
    | sort -n | tail -n 1 | cut -d ' ' -f 2-
}

CPU_TEST_RUNNER_PATH="$(latest_deploy onnx_test_runner)"
CPU_PERF_TEST_PATH="$(latest_deploy onnxruntime_perf_test)"
CPU_RUNTIME_LIBRARY_PATH="$(latest_deploy libonnxruntime.so.1.18.1)"
CPU_RUNTIME_PACKAGE_PATH="$({
  find "$PETALINUX_PROJECT/build/tmp/deploy/rpm" -type f \
    -name 'onnxruntime-cpu-tools-[0-9]*.rpm' -printf '%T@ %p\n' 2>/dev/null || true
} | sort -n | tail -n 1 | cut -d ' ' -f 2-)"
export CPU_TEST_RUNNER_PATH CPU_PERF_TEST_PATH CPU_RUNTIME_LIBRARY_PATH
export CPU_RUNTIME_PACKAGE_PATH

for output in "$CPU_TEST_RUNNER_PATH" "$CPU_PERF_TEST_PATH" \
  "$CPU_RUNTIME_LIBRARY_PATH" "$CPU_RUNTIME_PACKAGE_PATH"; do
  if [[ -z "$output" || ! -f "$output" ]]; then
    pl_finish_fail "missing packaged ONNX Runtime output: $output"
  fi
done

{
  echo "PetaLinux ONNX Runtime CPU package passed"
  echo "  correctness runner: $CPU_TEST_RUNNER_PATH"
  echo "  performance runner: $CPU_PERF_TEST_PATH"
  echo "  runtime library: $CPU_RUNTIME_LIBRARY_PATH"
  echo "  package: $CPU_RUNTIME_PACKAGE_PATH"
  sha256sum "$CPU_TEST_RUNNER_PATH" "$CPU_PERF_TEST_PATH" \
    "$CPU_RUNTIME_LIBRARY_PATH" "$CPU_RUNTIME_PACKAGE_PATH"
} | tee -a "$BUILD_LOG"

pl_write_manifest "pass"
