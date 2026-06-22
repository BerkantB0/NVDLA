#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WHAT="${1:-nvdla-sw}"

json_value() {
  local expr="$1"
  python3 - "$expr" <<'PY'
import json
import sys
expr = sys.argv[1].split(".")
with open("repro.lock.json", "r", encoding="utf-8") as f:
    data = json.load(f)
for key in expr:
    data = data[key]
print(data)
PY
}

fetch_repo() {
  local name="$1"
  local key="$2"
  local url commit path
  url="$(json_value "sources.$key.url")"
  commit="$(json_value "sources.$key.commit")"
  path="$(json_value "sources.$key.local_path")"

  mkdir -p "$(dirname "$path")"
  if [[ ! -d "$path/.git" ]]; then
    echo "Cloning $name into $path"
    git clone --no-checkout "$url" "$path"
  fi
  echo "Fetching $name commit $commit"
  git -C "$path" fetch --depth 1 origin "$commit"
  git -C "$path" checkout --detach "$commit"
  git -C "$path" rev-parse HEAD
}

case "$WHAT" in
  nvdla-sw)
    fetch_repo "nvdla/sw" nvdla_sw
    ;;
  linux-xlnx)
    fetch_repo "linux-xlnx" linux_xlnx
    ;;
  buildroot)
    fetch_repo "Buildroot" buildroot
    ;;
  all)
    fetch_repo "nvdla/sw" nvdla_sw
    fetch_repo "linux-xlnx" linux_xlnx
    fetch_repo "Buildroot" buildroot
    ;;
  *)
    echo "Usage: $0 [nvdla-sw|linux-xlnx|buildroot|all]" >&2
    exit 2
    ;;
esac

