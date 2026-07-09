#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/petalinux_common.sh"

cd "$ROOT"
pl_start_run "project"

if ! pl_source_settings; then
  pl_finish_fail "failed to source PetaLinux settings"
fi

if [[ ! -f "$XSA_PATH" ]]; then
  pl_finish_fail "XSA not found: $XSA_PATH"
fi

project_parent="$(dirname "$PETALINUX_PROJECT")"
project_name="$(basename "$PETALINUX_PROJECT")"
mkdir -p "$project_parent"

if [[ ! -d "$PETALINUX_PROJECT" ]]; then
  {
    echo "Creating PetaLinux zynqMP project"
    echo "  project: $PETALINUX_PROJECT"
    echo "  xsa: $XSA_PATH"
  } | tee "$BUILD_LOG"
  (
    cd "$project_parent"
    petalinux-create project -n "$project_name" --template zynqMP
  ) 2>&1 | tee -a "$BUILD_LOG" || pl_finish_fail "petalinux-create project failed"
else
  echo "Using existing PetaLinux project: $PETALINUX_PROJECT" | tee "$BUILD_LOG"
fi

if [[ ! -d "$PETALINUX_PROJECT/project-spec/meta-user" ]]; then
  pl_finish_fail "project missing project-spec/meta-user after creation"
fi

project_xsa=""
if [[ -d "$PETALINUX_PROJECT/project-spec/hw-description" ]]; then
  project_xsa="$(find "$PETALINUX_PROJECT/project-spec/hw-description" -maxdepth 1 -type f -name '*.xsa' | sort | head -n 1 || true)"
fi

expected_sha="$(sha256sum "$XSA_PATH" | awk '{print toupper($1)}')"
actual_sha=""
if [[ -n "$project_xsa" ]]; then
  actual_sha="$(sha256sum "$project_xsa" | awk '{print toupper($1)}')"
fi

if [[ "$actual_sha" != "$expected_sha" ]]; then
  {
    echo "Importing hardware description"
    echo "  expected XSA SHA256: $expected_sha"
    if [[ -n "$actual_sha" ]]; then
      echo "  previous project XSA SHA256: $actual_sha"
    fi
  } | tee -a "$BUILD_LOG"
  petalinux-config -p "$PETALINUX_PROJECT" --get-hw-description "$XSA_PATH" --silentconfig \
    2>&1 | tee -a "$BUILD_LOG" || pl_finish_fail "petalinux-config --get-hw-description failed"
fi

project_xsa="$(find "$PETALINUX_PROJECT/project-spec/hw-description" -maxdepth 1 -type f -name '*.xsa' | sort | head -n 1 || true)"
if [[ -z "$project_xsa" ]]; then
  pl_finish_fail "project hardware description does not contain an XSA"
fi
actual_sha="$(sha256sum "$project_xsa" | awk '{print toupper($1)}')"
if [[ "$actual_sha" != "$expected_sha" ]]; then
  pl_finish_fail "project XSA hash mismatch: expected $expected_sha got $actual_sha"
fi

echo "PetaLinux project ready: $PETALINUX_PROJECT" | tee -a "$BUILD_LOG"
pl_write_manifest "pass"
