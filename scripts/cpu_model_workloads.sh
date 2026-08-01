#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SOURCES_DIR="${SOURCES_DIR:-$ROOT/.external/sources}"
OUT_DIR="${CPU_WORKLOAD_DIR:-$ROOT/artifacts/workloads/cpu_onnx}"
IMAGE="${CPU_MODEL_IMAGE:-nvdla/onnx-models:1.18.1}"
STAGE_DIR="${CPU_MODEL_STAGE_DIR:-$ROOT/.work/cpu-model-input}"
LENET_SOURCE="$SOURCES_DIR/lenet-mnist"
RESNET_SOURCE="$SOURCES_DIR/resnet50-msra"
RESNET_WORKLOAD="$ROOT/artifacts/workloads/resnet50_small"

DOCKER="${DOCKER:-docker}"
if ! command -v "$DOCKER" >/dev/null 2>&1 ||
   ! "$DOCKER" version --format '{{.Server.Version}}' >/dev/null 2>&1; then
  if command -v docker.exe >/dev/null 2>&1; then
    DOCKER=docker.exe
  else
    echo "Docker CLI is unavailable (checked docker and docker.exe)" >&2
    exit 2
  fi
fi

docker_host_path() {
  if [[ "$DOCKER" == *.exe ]]; then
    wslpath -w "$1"
  else
    printf '%s\n' "$1"
  fi
}

for path in "$LENET_SOURCE" "$RESNET_SOURCE" "$RESNET_WORKLOAD"; do
  if [[ ! -d "$path" ]]; then
    echo "Missing CPU model input directory: $path" >&2
    exit 2
  fi
done

mkdir -p "$STAGE_DIR/lenet" "$STAGE_DIR/resnet50" "$ROOT/.work"
cp "$LENET_SOURCE/lenet_mnist.prototxt" "$STAGE_DIR/lenet/"
cp "$LENET_SOURCE/lenet_mnist.caffemodel" "$STAGE_DIR/lenet/"
cp "$LENET_SOURCE/lenet_mnist.json" "$STAGE_DIR/lenet/"
cp "$LENET_SOURCE/seven.pgm" "$STAGE_DIR/lenet/"
cp "$RESNET_SOURCE/ResNet-50-deploy.prototxt" "$STAGE_DIR/resnet50/"
cp "$RESNET_SOURCE/ResNet-50-model.caffemodel" "$STAGE_DIR/resnet50/"
cp "$RESNET_SOURCE/cat.jpg" "$STAGE_DIR/resnet50/"
cp "$RESNET_WORKLOAD/cat.center-crop-224.jpg" "$STAGE_DIR/resnet50/"

BUILD_OUT="$(mktemp -d "$ROOT/.work/cpu-onnx-build.XXXXXX")"

CONTEXT_HOST="$(docker_host_path "$ROOT/containers/onnx-models")"
TOOL_HOST="$(docker_host_path "$ROOT/tools/onnx/prepare_cpu_models.py")"
LOCK_HOST="$(docker_host_path "$ROOT/repro.lock.json")"
STAGE_HOST="$(docker_host_path "$STAGE_DIR")"
OUT_HOST="$(docker_host_path "$BUILD_OUT")"

"$DOCKER" build --pull=false -t "$IMAGE" "$CONTEXT_HOST"

"$DOCKER" run --rm \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$TOOL_HOST,dst=/tool/prepare_cpu_models.py,readonly" \
  --mount "type=bind,src=$LOCK_HOST,dst=/tool/repro.lock.json,readonly" \
  --mount "type=bind,src=$STAGE_HOST,dst=/inputs,readonly" \
  --mount "type=bind,src=$OUT_HOST,dst=/out" \
  "$IMAGE" \
  python /tool/prepare_cpu_models.py \
    --lock /tool/repro.lock.json \
    --lenet-source /inputs/lenet \
    --resnet-source /inputs/resnet50 \
    --resnet-workload /inputs/resnet50 \
    --out /out \
  | tee "$BUILD_OUT/build.log"

"$DOCKER" image inspect --format '{{.Id}}' "$IMAGE" >"$BUILD_OUT/container-image-id.txt"
(
  cd "$BUILD_OUT"
  sha256sum lenet/*/model.onnx resnet50/*/model.onnx
) >"$BUILD_OUT/model-sha256.txt"

mkdir -p "$(dirname "$OUT_DIR")"
BACKUP=""
if [[ -e "$OUT_DIR" ]]; then
  BACKUP="$ROOT/.work/cpu-onnx-previous.$$"
  mv "$OUT_DIR" "$BACKUP"
fi
if ! mv "$BUILD_OUT" "$OUT_DIR"; then
  [[ -z "$BACKUP" ]] || mv "$BACKUP" "$OUT_DIR"
  exit 1
fi
if [[ -n "$BACKUP" ]]; then
  WORK_ROOT_REAL="$(realpath "$ROOT/.work")"
  BACKUP_REAL="$(realpath "$BACKUP")"
  case "$BACKUP_REAL" in
    "$WORK_ROOT_REAL"/*) rm -rf -- "$BACKUP_REAL" ;;
    *) echo "Refusing to remove unexpected backup path: $BACKUP_REAL" >&2; exit 1 ;;
  esac
fi

echo "CPU ONNX workloads ready: $OUT_DIR"
