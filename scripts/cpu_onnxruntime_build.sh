#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PETALINUX_PROJECT="${PETALINUX_PROJECT:-$HOME/build/nvdla-peta/petalinux/zcu102-nvdla}"
SOURCES_DIR="${SOURCES_DIR:-$ROOT/.external/sources}"
WORK_DIR="${CPU_RUNTIME_WORK_DIR:-$HOME/build/nvdla-peta/cpu-onnxruntime}"
SOURCE="$SOURCES_DIR/onnxruntime"
EIGEN_SOURCE="$SOURCES_DIR/eigen"
SDK_ENV="${PETALINUX_SDK_ENV:-$PETALINUX_PROJECT/sdk/environment-setup-cortexa72-cortexa53-xilinx-linux}"
TOOLCHAIN="$ROOT/configs/petalinux/onnxruntime-aarch64-toolchain.cmake"
BUILD_DIR="$WORK_DIR/build"
CONFIG_DIR="$BUILD_DIR/Release"
INSTALL_DIR="$WORK_DIR/install"
LOG_DIR="$WORK_DIR/logs"
HOST_TOOLS="$WORK_DIR/host-tools"
JOBS="${JOBS:-$(nproc)}"
ARTIFACTS="${ARTIFACTS_DIR:-$ROOT/artifacts}"

expected_commit="$(python3 -c 'import json; print(json.load(open("repro.lock.json"))["sources"]["onnxruntime"]["commit"])')"
if [[ ! -d "$SOURCE/.git" ]]; then
  echo "Missing ONNX Runtime source: $SOURCE" >&2
  echo "Run make sources-onnxruntime with the same SOURCES_DIR." >&2
  exit 2
fi
actual_commit="$(git -C "$SOURCE" rev-parse HEAD)"
if [[ "$actual_commit" != "$expected_commit" ]]; then
  echo "ONNX Runtime source mismatch: expected $expected_commit, got $actual_commit" >&2
  exit 2
fi
expected_eigen_commit="$(python3 -c 'import json; print(json.load(open("repro.lock.json"))["sources"]["eigen"]["commit"])')"
if [[ ! -d "$EIGEN_SOURCE/.git" ]]; then
  echo "Missing Eigen source: $EIGEN_SOURCE" >&2
  echo "Run make sources-eigen with the same SOURCES_DIR." >&2
  exit 2
fi
actual_eigen_commit="$(git -C "$EIGEN_SOURCE" rev-parse HEAD)"
if [[ "$actual_eigen_commit" != "$expected_eigen_commit" ]]; then
  echo "Eigen source mismatch: expected $expected_eigen_commit, got $actual_eigen_commit" >&2
  exit 2
fi
if [[ ! -f "$SDK_ENV" ]]; then
  echo "Missing PetaLinux SDK environment: $SDK_ENV" >&2
  exit 2
fi

mkdir -p "$BUILD_DIR" "$INSTALL_DIR" "$LOG_DIR"
cmake_version="$(python3 -c 'import json; print(json.load(open("repro.lock.json"))["cpu_benchmark"]["build_tools"]["cmake"])')"
ninja_version="$(python3 -c 'import json; print(json.load(open("repro.lock.json"))["cpu_benchmark"]["build_tools"]["ninja"])')"
if [[ ! -x "$HOST_TOOLS/bin/cmake" ]] ||
   [[ "$($HOST_TOOLS/bin/cmake --version | sed -n '1s/^cmake version //p')" != "$cmake_version" ]]; then
  python3 -m venv "$HOST_TOOLS"
  "$HOST_TOOLS/bin/python" -m pip install --disable-pip-version-check \
    "cmake==$cmake_version" "ninja==$ninja_version"
fi

build_profile="onnxruntime-aarch64-cpu-v3-prefix-map-response"
prefix_map_target="/usr/src/nvdla-cpu-build"
prefix_map_spec="-ffile-prefix-map=$HOME=$prefix_map_target -fmacro-prefix-map=$HOME=$prefix_map_target"
prefix_map_response="/dev/shm/nvdla-onnxruntime-prefix-map.flags"
prefix_map_flags="@$prefix_map_response"
config_fingerprint="$({
  echo "$build_profile"
  echo "$prefix_map_spec"
  echo "$actual_commit"
  echo "$actual_eigen_commit"
  sha256sum "$TOOLCHAIN" "$SDK_ENV"
  echo "$cmake_version"
  echo "$ninja_version"
} | sha256sum | cut -d ' ' -f 1)"
fingerprint_file="$WORK_DIR/build-config.sha256"
previous_fingerprint="$(cat "$fingerprint_file" 2>/dev/null || true)"
if [[ "$previous_fingerprint" != "$config_fingerprint" ]]; then
  echo "ONNX Runtime build configuration changed; resetting generated outputs"
  rm -rf "$BUILD_DIR" "$INSTALL_DIR" "$LOG_DIR"
  mkdir -p "$BUILD_DIR" "$INSTALL_DIR" "$LOG_DIR"
  printf '%s\n' "$config_fingerprint" >"$fingerprint_file"
fi

unset LD_LIBRARY_PATH
# shellcheck disable=SC1090
source "$SDK_ENV" >/dev/null
export PATH="$HOST_TOOLS/bin:$PATH"
printf '%s\n' "$prefix_map_spec" >"$prefix_map_response"
trap 'rm -f "$prefix_map_response"' EXIT
export ONNXRUNTIME_PREFIX_MAP_FLAGS="$prefix_map_flags"

echo "Configuring ONNX Runtime $actual_commit for PetaLinux AArch64" \
  | tee "$LOG_DIR/configure.log"
python3 "$SOURCE/tools/ci_build/build.py" \
  --config Release \
  --update \
  --build_dir "$BUILD_DIR" \
  --arm64 \
  --build_shared_lib \
  --compile_no_warning_as_error \
  --skip_submodule_sync \
  --use_preinstalled_eigen \
  --eigen_path "$EIGEN_SOURCE" \
  --cmake_generator Ninja \
  --cmake_extra_defines \
    "CMAKE_TOOLCHAIN_FILE=$TOOLCHAIN" \
    "CMAKE_INSTALL_PREFIX=/usr" \
    "CMAKE_BUILD_WITH_INSTALL_RPATH=ON" \
    "CMAKE_INSTALL_RPATH=" \
    "onnxruntime_ENABLE_LTO=OFF" \
  2>&1 | tee -a "$LOG_DIR/configure.log"

echo "Building standard ONNX Runtime library and benchmark tools" \
  | tee "$LOG_DIR/build.log"
cmake --build "$CONFIG_DIR" \
  --parallel "$JOBS" \
  --target onnxruntime onnx_test_runner onnxruntime_perf_test \
  2>&1 | tee -a "$LOG_DIR/build.log"

runtime_library="$(find "$CONFIG_DIR" -maxdepth 2 -type f -name 'libonnxruntime.so.*' -print | sort | tail -n 1)"
test_runner="$(find "$CONFIG_DIR" -maxdepth 2 -type f -name onnx_test_runner -print | sort | tail -n 1)"
perf_test="$(find "$CONFIG_DIR" -maxdepth 2 -type f -name onnxruntime_perf_test -print | sort | tail -n 1)"
for path in "$runtime_library" "$test_runner" "$perf_test"; do
  if [[ -z "$path" || ! -f "$path" ]]; then
    echo "Missing expected ONNX Runtime build output: $path" >&2
    exit 1
  fi
done

install -m 0755 "$runtime_library" "$INSTALL_DIR/libonnxruntime.so.1.18.1"
install -m 0755 "$test_runner" "$INSTALL_DIR/onnx_test_runner"
install -m 0755 "$perf_test" "$INSTALL_DIR/onnxruntime_perf_test"
ln -sfn libonnxruntime.so.1.18.1 "$INSTALL_DIR/libonnxruntime.so.1"
ln -sfn libonnxruntime.so.1 "$INSTALL_DIR/libonnxruntime.so"

{
  echo "source_commit=$actual_commit"
  echo "eigen_commit=$actual_eigen_commit"
  echo "sdk_version=${OECORE_SDK_VERSION:-unknown}"
  echo "target_arch=${OECORE_TARGET_ARCH:-unknown}"
  echo "target_sysroot=$SDKTARGETSYSROOT"
  echo "cmake=$(cmake --version | sed -n '1p')"
  echo "ninja=$(ninja --version)"
  echo "compiler=$($CXX --version | head -n 1)"
  echo "toolchain_sha256=$(sha256sum "$TOOLCHAIN" | cut -d ' ' -f 1)"
  sha256sum \
    "$INSTALL_DIR/libonnxruntime.so.1.18.1" \
    "$INSTALL_DIR/onnx_test_runner" \
    "$INSTALL_DIR/onnxruntime_perf_test"
} >"$INSTALL_DIR/build-evidence.txt"

for path in \
  "$INSTALL_DIR/libonnxruntime.so.1.18.1" \
  "$INSTALL_DIR/onnx_test_runner" \
  "$INSTALL_DIR/onnxruntime_perf_test"; do
  "$READELF" -h "$path"
  "$READELF" -d "$path"
done >"$INSTALL_DIR/readelf.txt"

if grep -E '\((RPATH|RUNPATH)\)' "$INSTALL_DIR/readelf.txt" \
  | grep -Fv '[$ORIGIN]' >"$INSTALL_DIR/unsafe-rpaths.txt"; then
  cat "$INSTALL_DIR/unsafe-rpaths.txt" >&2
  echo "ONNX Runtime outputs contain an RPATH/RUNPATH other than literal \$ORIGIN" >&2
  exit 1
fi
rm -f "$INSTALL_DIR/unsafe-rpaths.txt"

host_path_report="$INSTALL_DIR/host-paths.txt"
: >"$host_path_report"
for path in \
  "$INSTALL_DIR/libonnxruntime.so.1.18.1" \
  "$INSTALL_DIR/onnx_test_runner" \
  "$INSTALL_DIR/onnxruntime_perf_test"; do
  "${STRINGS:-strings}" -a "$path" \
    | grep -E '/home/|/mnt/|/tmp/work/|/build/tmp/' \
    >>"$host_path_report" || true
done
if [[ -s "$host_path_report" ]]; then
  echo "ONNX Runtime outputs contain host build paths:" >&2
  cat "$host_path_report" >&2
  exit 1
fi
rm -f "$host_path_report"

RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-cpu-onnxruntime}"
RUN_DIR="$ARTIFACTS/$RUN_ID"
mkdir -p "$RUN_DIR"
cp "$LOG_DIR/configure.log" "$LOG_DIR/build.log" "$RUN_DIR/"
cp "$INSTALL_DIR/build-evidence.txt" "$INSTALL_DIR/readelf.txt" "$RUN_DIR/"
export RUN_DIR INSTALL_DIR SOURCE EIGEN_SOURCE TOOLCHAIN SDK_ENV
export ACTUAL_COMMIT="$actual_commit" ACTUAL_EIGEN_COMMIT="$actual_eigen_commit"
python3 - <<'PY'
import hashlib
import json
import os
import re
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


install = Path(os.environ["INSTALL_DIR"])
outputs = {}
for name in ("libonnxruntime.so.1.18.1", "onnx_test_runner", "onnxruntime_perf_test"):
    path = install / name
    outputs[name] = {
        "path": str(path),
        "sha256": sha256(path),
        "size_bytes": path.stat().st_size,
    }

readelf = (install / "readelf.txt").read_text(encoding="utf-8", errors="replace")
machines = sorted(set(re.findall(r"^\s*Machine:\s*(.+)$", readelf, re.MULTILINE)))
needed = sorted(set(re.findall(r"Shared library: \[(.+?)\]", readelf)))
rpaths = sorted(set(re.findall(r"Library (?:rpath|runpath): \[(.+?)\]", readelf)))
manifest = {
    "schema_version": 1,
    "lane": "cpu-onnxruntime",
    "status": "pass",
    "sources": {
        "onnxruntime_commit": os.environ["ACTUAL_COMMIT"],
        "eigen_commit": os.environ["ACTUAL_EIGEN_COMMIT"],
    },
    "build": {
        "sdk_environment": os.environ["SDK_ENV"],
        "toolchain_file": os.environ["TOOLCHAIN"],
        "toolchain_sha256": sha256(Path(os.environ["TOOLCHAIN"])),
    },
    "elf": {
        "machines": machines,
        "needed": needed,
        "rpaths": rpaths,
        "rpath_policy": "literal-$ORIGIN-only",
        "host_path_policy": "no-/home-/mnt-/tmp/work-/build/tmp-paths",
    },
    "outputs": outputs,
    "logs": ["configure.log", "build.log", "build-evidence.txt", "readelf.txt"],
}
path = Path(os.environ["RUN_DIR"]) / "manifest.json"
path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"Wrote {path}")
PY

echo "ONNX Runtime CPU tools ready: $INSTALL_DIR"
