#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT/tools:${PYTHONPATH:-}"
ARTIFACTS="${ARTIFACTS_DIR:-$ROOT/artifacts}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ID="${RUN_ID:-$STAMP-petalinux-board-payload}"
RUN_DIR="$ARTIFACTS/$RUN_ID"
WORKLOADS_DIR="${WORKLOADS_DIR:-$ROOT/artifacts/workloads}"
PAYLOAD_DIR="$RUN_DIR/nvdla-tests"
ARCHIVE_PATH="$RUN_DIR/nvdla-tests.tar.gz"
MANIFEST_PATH="$RUN_DIR/manifest.json"

mkdir -p "$RUN_DIR"
cd "$ROOT"

python3 -m nvdla_test_framework board-payload \
  --workloads-dir "$WORKLOADS_DIR" \
  --out-dir "$PAYLOAD_DIR" \
  --archive "$ARCHIVE_PATH" \
  --manifest "$MANIFEST_PATH"

echo "PetaLinux board workload payload passed"
echo "  copy directory: $PAYLOAD_DIR"
echo "  archive: $ARCHIVE_PATH"
sha256sum "$ARCHIVE_PATH"
