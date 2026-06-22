#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PETALINUX_DIR="${PETALINUX_DIR:-/opt/pkg/petalinux/2024.1}"
PROJECT="${PETALINUX_PROJECT:-}"

if [[ -z "$PROJECT" ]]; then
  echo "ERROR: PETALINUX_PROJECT must point to an existing PetaLinux project" >&2
  exit 2
fi
if [[ ! -d "$PROJECT/project-spec/meta-user" ]]; then
  echo "ERROR: not a PetaLinux project or missing meta-user: $PROJECT" >&2
  exit 2
fi
if [[ ! -f "$PETALINUX_DIR/settings.sh" ]]; then
  echo "ERROR: PetaLinux settings not found at $PETALINUX_DIR/settings.sh" >&2
  exit 2
fi

set +u
source "$PETALINUX_DIR/settings.sh"
set -u

DEST="$PROJECT/project-spec/meta-user/recipes-modules/opendla"
mkdir -p "$DEST"
cp -r "$ROOT/recipes/petalinux/modules/opendla/"* "$DEST/"

echo "Installed opendla recipe skeleton into $DEST"
echo "Building opendla in $PROJECT"
petalinux-build -p "$PROJECT" -c opendla

