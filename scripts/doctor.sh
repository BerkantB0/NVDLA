#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "Repository: $ROOT"

UNAME="$(uname -s 2>/dev/null || echo unknown)"
if [[ -r /proc/version ]] && grep -qi microsoft /proc/version; then
  echo "Host shell: WSL ($UNAME)"
  echo "WSL: detected"
  echo "WSL distro: ${WSL_DISTRO_NAME:-unknown}"
elif [[ "$UNAME" == MINGW* || "$UNAME" == MSYS* || "$UNAME" == CYGWIN* ]]; then
  echo "Host shell: Windows POSIX layer ($UNAME)"
  echo "WARNING: WSL not detected in this shell; run the full framework from Ubuntu WSL" >&2
else
  echo "Host shell: Linux/other ($UNAME)"
  echo "WARNING: WSL not detected in this shell; this framework is designed for Ubuntu WSL" >&2
fi
if [[ -r /etc/os-release ]]; then
  . /etc/os-release
  echo "OS release: ${PRETTY_NAME:-unknown}"
fi

if command -v wsl.exe >/dev/null 2>&1; then
  echo "wsl.exe: $(command -v wsl.exe)"
  if ! wsl.exe --list --verbose >/tmp/nvdla-wsl-list.log 2>&1; then
    echo "WARNING: wsl.exe is present but this launcher could not list distros; continuing with current shell facts" >&2
  fi
fi

for tool in python3 git make; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "ERROR: missing required tool: $tool" >&2
    exit 1
  fi
  echo "$tool: $(command -v "$tool")"
done

if command -v docker >/dev/null 2>&1; then
  echo "docker: $(command -v docker)"
  if ! docker version --format 'Docker: client {{.Client.Version}}, server {{.Server.Version}}'; then
    echo "WARNING: Docker is present but unavailable in this WSL distro; VP targets remain the hard Docker gate" >&2
  fi
else
  echo "WARNING: Docker not found in this shell; VP targets remain the hard Docker gate" >&2
fi

PETALINUX_DIR="${PETALINUX_DIR:-/opt/pkg/petalinux/2024.1}"
PETALINUX_PROJECT="${PETALINUX_PROJECT:-$HOME/build/nvdla-peta/petalinux/zcu102-nvdla}"
if [[ ! -f "$PETALINUX_DIR/settings.sh" ]]; then
  echo "ERROR: PetaLinux settings not found at $PETALINUX_DIR/settings.sh" >&2
  exit 1
fi
echo "PetaLinux install: $PETALINUX_DIR"
echo "PetaLinux project default: $PETALINUX_PROJECT"

set +e +u
source "$PETALINUX_DIR/settings.sh" >/tmp/nvdla-petalinux-settings.log 2>&1
settings_status=$?
set -euo pipefail
if [[ "$settings_status" -ne 0 ]]; then
  echo "WARNING: PetaLinux settings returned $settings_status; verifying tool environment" >&2
fi

for tool in petalinux-build petalinux-config petalinux-create petalinux-package; do
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
