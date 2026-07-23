#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT/tools:${PYTHONPATH:-}"
ARTIFACTS="${ARTIFACTS_DIR:-$ROOT/artifacts}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="${BOARD_RUN_DIR:-$ARTIFACTS/$STAMP-petalinux-board-import}"
mkdir -p "$RUN_DIR"

LOCAL_ARCHIVE="${BOARD_ARCHIVE_LOCAL:-}"
if [[ -z "$LOCAL_ARCHIVE" ]]; then
  BOARD_HOST="${BOARD_HOST:-}"
  BOARD_USER="${BOARD_USER:-root}"
  BOARD_SSH_PORT="${BOARD_SSH_PORT:-22}"
  BOARD_REMOTE_ARCHIVE="${BOARD_REMOTE_ARCHIVE:-/tmp/nvdla-board-latest.tar.gz}"
  if [[ -z "$BOARD_HOST" ]]; then
    echo "ERROR: set BOARD_ARCHIVE_LOCAL or BOARD_HOST" >&2
    exit 2
  fi
  LOCAL_ARCHIVE="$RUN_DIR/board-download.tar.gz"
  scp -P "$BOARD_SSH_PORT" -o StrictHostKeyChecking=accept-new \
    "$BOARD_USER@$BOARD_HOST:$BOARD_REMOTE_ARCHIVE" "$LOCAL_ARCHIVE"
fi

args=(
  board-artifact-import
  --archive "$LOCAL_ARCHIVE"
  --out "$RUN_DIR"
)
if [[ -n "${BOARD_SERIAL_LOG:-}" ]]; then
  args+=(--serial-log "$BOARD_SERIAL_LOG")
fi

python3 -m nvdla_test_framework "${args[@]}"
