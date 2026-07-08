#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ACTION="${1:-}"
if [[ -z "$ACTION" ]]; then
  echo "Usage: $0 {cmod|bin|verify|all}" >&2
  exit 2
fi

SOURCES="${SOURCES_DIR:-$HOME/src/nvdla-peta-sources}"
WORK="${WORK_DIR:-$HOME/build/nvdla-peta/vp-modern}"
IMAGE="${VP_SMALL_DOCKER_IMAGE:-nvdla/vp:latest}"
CONTAINER_SYSTEMC="${VP_SMALL_DOCKER_SYSTEMC_PREFIX:-/usr/local/systemc-2.3.0}"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker not found; run the host target with SYSTEMC_PREFIX or install Docker." >&2
  exit 2
fi

mkdir -p "$SOURCES" "$WORK"

docker_args=(
  run --rm
  -u "$(id -u):$(id -g)"
  -e HOME=/tmp
  -e SOURCES_DIR=/sources
  -e WORK_DIR=/work
  -e "SYSTEMC_PREFIX=$CONTAINER_SYSTEMC"
  -e "VP_HW_PROJECT=${VP_HW_PROJECT:-nv_small}"
  -e "VP_CMAKE_BUILD_TYPE=${VP_CMAKE_BUILD_TYPE:-Debug}"
  -e "VP_DISABLE_WERROR=${VP_DISABLE_WERROR:-1}"
  -e "NVDLA_CMOD_CXXFLAGS=${NVDLA_CMOD_CXXFLAGS:--Wno-error}"
)
if [[ -n "${RUN_ID:-}" ]]; then
  docker_args+=(-e "RUN_ID=$RUN_ID")
fi
docker_args+=(
  -v "$ROOT:/repo"
  -v "$SOURCES:/sources:ro"
  -v "$WORK:/work"
  -w /repo
  "$IMAGE"
  scripts/vp_small_build.sh "$ACTION"
)

exec docker "${docker_args[@]}"
