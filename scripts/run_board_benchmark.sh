#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 {cpu|nvdla} {lenet|resnet50} [output-directory]" >&2
  exit 2
}

[[ $# -ge 2 && $# -le 3 ]] || usage
KIND="$1"
MODEL="$2"
case "$KIND" in cpu|nvdla) ;; *) usage ;; esac
case "$MODEL" in lenet|resnet50) ;; *) usage ;; esac

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${3:-$ROOT/artifacts/${KIND}-board-ssh}"
HOST="${NVDLA_BOARD_HOST:-192.168.50.2}"
USER="${NVDLA_BOARD_USER:-root}"
PAYLOAD="${NVDLA_BOARD_PAYLOAD:-/run/media/ROOT-mmcblk0p1/nvdla-tests}"
STATE="$ROOT/.work/${KIND}-board-last-boot-id"
export SSHPASS="${NVDLA_BOARD_PASSWORD:-nvdla}"

command -v sshpass >/dev/null || {
  echo "sshpass is required (Ubuntu: sudo apt install sshpass)" >&2
  exit 1
}

SSH=(sshpass -e ssh -o BatchMode=no -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -o LogLevel=ERROR)
SCP=(sshpass -e scp -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -o LogLevel=ERROR)
TARGET="$USER@$HOST"

for _ in {1..60}; do
  "${SSH[@]}" "$TARGET" true >/dev/null 2>&1 && break
  sleep 1
done
"${SSH[@]}" "$TARGET" true >/dev/null

BOOT_ID="$("${SSH[@]}" "$TARGET" cat /proc/sys/kernel/random/boot_id)"
if [[ -f "$STATE" && "$(cat "$STATE")" == "$BOOT_ID" ]]; then
  echo "refusing to reuse $KIND benchmark boot $BOOT_ID; reboot the board first" >&2
  exit 1
fi

if [[ "$KIND" == cpu ]]; then
  COMMAND="nvdla-board-cpu-benchmark '$MODEL' '$PAYLOAD' --precision fp32 --threads 4 --regime all"
  LATEST=/tmp/nvdla-board-cpu-benchmark-latest.tar.gz
  NAME="cpu-${MODEL}-fp32-4t"
else
  COMMAND="nvdla-board-benchmark '$MODEL' '$PAYLOAD' --regime all"
  LATEST=/tmp/nvdla-board-benchmark-latest.tar.gz
  NAME="nvdla-${MODEL}"
fi

echo "Running $KIND $MODEL benchmark on boot $BOOT_ID"
"${SSH[@]}" "$TARGET" "$COMMAND"

mkdir -p "$OUT_DIR" "$(dirname "$STATE")"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="$OUT_DIR/${NAME}-${STAMP}-${BOOT_ID}.tar.gz"
REMOTE_ARCHIVE="$("${SSH[@]}" "$TARGET" readlink -f "$LATEST")"
"${SCP[@]}" "$TARGET:$REMOTE_ARCHIVE" "$ARCHIVE"
tar -tzf "$ARCHIVE" >/dev/null
printf '%s\n' "$BOOT_ID" >"$STATE"
sha256sum "$ARCHIVE"
echo "Saved $ARCHIVE"
