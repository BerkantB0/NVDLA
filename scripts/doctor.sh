#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "Repository: $ROOT"

UNAME="$(uname -s 2>/dev/null || echo unknown)"
if [[ -r /proc/version ]] && grep -qi microsoft /proc/version; then
  echo "Host shell: WSL ($UNAME)"
  echo "WSL: detected"
elif [[ "$UNAME" == MINGW* || "$UNAME" == MSYS* || "$UNAME" == CYGWIN* ]]; then
  echo "Host shell: Windows POSIX layer ($UNAME)"
  echo "WARNING: WSL not detected in this shell; run the full framework from Ubuntu WSL" >&2
else
  echo "Host shell: Linux/other ($UNAME)"
  echo "WARNING: WSL not detected in this shell; this framework is designed for Ubuntu WSL" >&2
fi

if command -v wsl.exe >/dev/null 2>&1; then
  echo "wsl.exe: $(command -v wsl.exe)"
  if ! wsl.exe --list --verbose >/tmp/nvdla-wsl-list.log 2>&1; then
    echo "WARNING: wsl.exe is present but this launcher could not list distros; continuing with current shell facts" >&2
  fi
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
