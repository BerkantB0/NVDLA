#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<EOF
usage: $0 {cpu|nvdla} {lenet|resnet50} [HOST OPTIONS] [BENCHMARK OPTIONS]

Host options:
  --ssh-host HOST          Board address (default: 192.168.50.2)
  --ssh-user USER          SSH user (default: root)
  --ssh-password PASSWORD  Test-image password (default: nvdla)
  --ssh-wait-seconds N     SSH startup timeout (default: 60)
  --payload PATH           Target payload path
  --output DIRECTORY       Host archive directory
  --help                   Show this help

All other options, including --power, are passed to the target benchmark.
EOF
  exit "${1:-2}"
}

[[ ${1:-} != --help && ${1:-} != -h ]] || usage 0
[[ $# -ge 2 ]] || usage
KIND="$1"
MODEL="$2"
shift 2
case "$KIND" in cpu|nvdla) ;; *) usage ;; esac
case "$MODEL" in lenet|resnet50) ;; *) usage ;; esac

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$ROOT/artifacts/${KIND}-board-ssh"
HOST=192.168.50.2
USER=root
PASSWORD=nvdla
SSH_WAIT_SECONDS=60
PAYLOAD=/run/media/ROOT-mmcblk0p1/nvdla-tests
BENCHMARK_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h) usage 0 ;;
    --ssh-host|--ssh-user|--ssh-password|--ssh-wait-seconds|--payload|--output)
      [[ $# -ge 2 ]] || { echo "$1 requires a value" >&2; exit 2; }
      case "$1" in
        --ssh-host) HOST="$2" ;;
        --ssh-user) USER="$2" ;;
        --ssh-password) PASSWORD="$2" ;;
        --ssh-wait-seconds) SSH_WAIT_SECONDS="$2" ;;
        --payload) PAYLOAD="$2" ;;
        --output) OUT_DIR="$2" ;;
      esac
      shift 2
      ;;
    --) shift; BENCHMARK_ARGS+=("$@"); break ;;
    *) BENCHMARK_ARGS+=("$1"); shift ;;
  esac
done

[[ "$SSH_WAIT_SECONDS" =~ ^[1-9][0-9]*$ ]] || {
  echo "--ssh-wait-seconds must be a positive integer" >&2
  exit 2
}
STATE="$ROOT/.work/${KIND}-board-last-boot-id"
export SSHPASS="$PASSWORD"

command -v sshpass >/dev/null || {
  echo "sshpass is required (Ubuntu: sudo apt install sshpass)" >&2
  exit 1
}

SSH=(sshpass -e ssh -o BatchMode=no -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -o LogLevel=ERROR)
SCP=(sshpass -e scp -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -o LogLevel=ERROR)
TARGET="$USER@$HOST"

for ((attempt = 0; attempt < SSH_WAIT_SECONDS; attempt++)); do
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
  REMOTE=(nvdla-board-cpu-benchmark "$MODEL" "$PAYLOAD" "${BENCHMARK_ARGS[@]}")
  LATEST=/tmp/nvdla-board-cpu-benchmark-latest.tar.gz
else
  REMOTE=(nvdla-board-benchmark "$MODEL" "$PAYLOAD" "${BENCHMARK_ARGS[@]}")
  LATEST=/tmp/nvdla-board-benchmark-latest.tar.gz
fi
NAME="${KIND}-${MODEL}"
[[ " ${BENCHMARK_ARGS[*]} " == *" --input-set multi20 "* ]] && NAME="${NAME}-multi20"
[[ " ${BENCHMARK_ARGS[*]} " == *" --power "* ]] && NAME="${NAME}-power"
printf -v COMMAND '%q ' "${REMOTE[@]}"

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
