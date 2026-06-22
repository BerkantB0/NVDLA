#!/usr/bin/env bash
set -euo pipefail

PETALINUX_DIR="${PETALINUX_DIR:-/opt/pkg/petalinux/2024.1}"

if [[ ! -f "$PETALINUX_DIR/settings.sh" ]]; then
  echo "ERROR: PetaLinux settings not found at $PETALINUX_DIR/settings.sh" >&2
  exit 1
fi

set +u
source "$PETALINUX_DIR/settings.sh" >/tmp/nvdla-petalinux-smoke.log 2>&1
set -u

for tool in petalinux-build petalinux-create petalinux-package; do
  command -v "$tool" >/dev/null
done

echo "PetaLinux smoke passed: $PETALINUX_DIR"

