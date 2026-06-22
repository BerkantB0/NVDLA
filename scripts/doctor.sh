#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "Repository: $ROOT"

if [[ -r /proc/version ]] && grep -qi microsoft /proc/version; then
  echo "WSL: detected"
else
  echo "WARNING: WSL not detected; this framework is designed for Ubuntu WSL" >&2
fi

for tool in python3 git make docker; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "ERROR: missing required tool: $tool" >&2
    exit 1
  fi
  echo "$tool: $(command -v "$tool")"
done

docker version --format 'Docker: client {{.Client.Version}}, server {{.Server.Version}}'

PETALINUX_DIR="${PETALINUX_DIR:-/opt/pkg/petalinux/2024.1}"
if [[ ! -f "$PETALINUX_DIR/settings.sh" ]]; then
  echo "ERROR: PetaLinux settings not found at $PETALINUX_DIR/settings.sh" >&2
  exit 1
fi

set +u
source "$PETALINUX_DIR/settings.sh" >/tmp/nvdla-petalinux-settings.log 2>&1
set -u

for tool in petalinux-build petalinux-create petalinux-package; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "ERROR: $tool not found after sourcing PetaLinux settings" >&2
    cat /tmp/nvdla-petalinux-settings.log >&2
    exit 1
  fi
  echo "$tool: $(command -v "$tool")"
done

if grep -qi 'not a supported OS' /tmp/nvdla-petalinux-settings.log; then
  echo "PetaLinux warning: unsupported host OS warning observed and recorded"
fi

echo "Doctor check passed"

