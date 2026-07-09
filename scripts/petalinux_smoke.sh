#!/usr/bin/env bash
set -euo pipefail

PETALINUX_DIR="${PETALINUX_DIR:-/opt/pkg/petalinux/2024.1}"
PETALINUX_PROJECT="${PETALINUX_PROJECT:-$HOME/build/nvdla-peta/petalinux/zcu102-nvdla}"

if [[ ! -f "$PETALINUX_DIR/settings.sh" ]]; then
  echo "ERROR: PetaLinux settings not found at $PETALINUX_DIR/settings.sh" >&2
  exit 1
fi

set +e +u
source "$PETALINUX_DIR/settings.sh" >/tmp/nvdla-petalinux-smoke.log 2>&1
settings_status=$?
set -euo pipefail
if [[ "$settings_status" -ne 0 ]]; then
  echo "WARNING: PetaLinux settings returned $settings_status; verifying tool environment" >&2
fi

for tool in petalinux-build petalinux-config petalinux-create petalinux-package; do
  command -v "$tool" >/dev/null
done

echo "PetaLinux smoke passed: $PETALINUX_DIR"
echo "PetaLinux project default: $PETALINUX_PROJECT"
