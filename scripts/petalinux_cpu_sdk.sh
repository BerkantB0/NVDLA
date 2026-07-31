#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/petalinux_common.sh"

cd "$ROOT"
pl_start_run "cpu-sdk"

if ! pl_source_settings; then
  pl_finish_fail "failed to source PetaLinux settings"
fi
pl_require_project

SDK_INSTALL_DIR="${PETALINUX_SDK_DIR:-$PETALINUX_PROJECT/sdk}"
SDK_ENV="$SDK_INSTALL_DIR/environment-setup-cortexa72-cortexa53-xilinx-linux"
SDK_INSTALLER="$PETALINUX_PROJECT/images/linux/sdk.sh"
export SDK_INSTALL_DIR SDK_ENV SDK_INSTALLER

if [[ ! -f "$SDK_ENV" ]]; then
  {
    echo "Exporting the PetaLinux SDK with memory-bounded parallelism"
    echo "  project: $PETALINUX_PROJECT"
    echo "  BB_NUMBER_THREADS=${BB_NUMBER_THREADS:-2}"
    echo "  PARALLEL_MAKE=${PARALLEL_MAKE:--j2}"
  } | tee "$BUILD_LOG"
  BB_NUMBER_THREADS="${BB_NUMBER_THREADS:-2}" \
    PARALLEL_MAKE="${PARALLEL_MAKE:--j2}" \
    petalinux-build -p "$PETALINUX_PROJECT" --sdk \
    2>&1 | tee -a "$BUILD_LOG" || pl_finish_fail "PetaLinux SDK export failed"

  if [[ ! -x "$SDK_INSTALLER" ]]; then
    pl_finish_fail "SDK export did not produce $SDK_INSTALLER"
  fi

  sh "$SDK_INSTALLER" -y -d "$SDK_INSTALL_DIR" \
    2>&1 | tee -a "$BUILD_LOG" || pl_finish_fail "PetaLinux SDK install failed"
else
  echo "Using installed PetaLinux SDK: $SDK_INSTALL_DIR" | tee "$BUILD_LOG"
fi

if [[ ! -f "$SDK_ENV" ]]; then
  pl_finish_fail "installed SDK environment is missing: $SDK_ENV"
fi

set +u
# shellcheck disable=SC1090
source "$SDK_ENV" >/dev/null
set -u

for tool in "${CC%% *}" "${CXX%% *}" "$READELF"; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    pl_finish_fail "installed SDK tool is unavailable: $tool"
  fi
done

{
  echo "sdk_install_dir=$SDK_INSTALL_DIR"
  echo "sdk_environment=$SDK_ENV"
  echo "sdk_version=${OECORE_SDK_VERSION:-unknown}"
  echo "target_arch=${OECORE_TARGET_ARCH:-unknown}"
  echo "target_sysroot=${SDKTARGETSYSROOT:-unknown}"
  echo "cc=$CC"
  echo "cxx=$CXX"
  echo "installer_sha256=$(sha256sum "$SDK_INSTALLER" | cut -d ' ' -f 1)"
  echo "environment_sha256=$(sha256sum "$SDK_ENV" | cut -d ' ' -f 1)"
} >"$RUN_DIR/sdk-evidence.txt"

echo "PetaLinux CPU SDK ready: $SDK_INSTALL_DIR" | tee -a "$BUILD_LOG"
pl_write_manifest "pass"
