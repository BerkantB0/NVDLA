#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ACTION="${1:-}"
SOURCES="${SOURCES_DIR:-$ROOT/.external/sources}"
SOURCE="${NVDLA_SW_SOURCE:-$SOURCES/nvdla-sw}"
WORK="${PATCHED_NVDLA_SW:-$ROOT/.work/nvdla-sw-patched}"
PATCH_DIR="${NVDLA_PATCH_DIR:-$ROOT/patches/nvdla-sw}"
EXTRA_PATCH_DIR="${NVDLA_EXTRA_PATCH_DIR:-}"
LINUX="${LINUX_SOURCE:-$SOURCES/linux-xlnx}"
BASE="$(python3 - <<'PY'
import json
with open("repro.lock.json", "r", encoding="utf-8") as f:
    print(json.load(f)["sources"]["nvdla_sw"]["commit"])
PY
)"
BRANCH="${NVDLA_PATCH_BRANCH:-modern-linux-support}"
LOCKDIR="$ROOT/.work/nvdla-patch-queue.lock"

usage() {
  cat >&2 <<EOF
Usage: $0 {prepare|apply|status|format|check}

prepare  Create/reset $WORK at the pinned upstream base without patches.
apply    Reset $WORK and apply patches from $PATCH_DIR with git am.
         If NVDLA_EXTRA_PATCH_DIR is set, apply that queue afterwards.
status   Print base, worktree, branch, and patch queue status.
format   Regenerate $PATCH_DIR/*.patch from commits after the base.
check    Verify patches apply and run linux-xlnx scripts/checkpatch.pl when present.
EOF
}

need_source() {
  if [[ ! -d "$SOURCE/.git" ]]; then
    echo "ERROR: pristine nvdla/sw checkout not found: $SOURCE" >&2
    echo "       Run: make sources" >&2
    exit 2
  fi
}

acquire_lock() {
  mkdir -p "$ROOT/.work"
  local waited=0
  while ! mkdir "$LOCKDIR" 2>/dev/null; do
    if [[ "$waited" -ge "${NVDLA_PATCH_LOCK_TIMEOUT:-120}" ]]; then
      echo "ERROR: timed out waiting for patch queue lock: $LOCKDIR" >&2
      echo "       Remove it only after confirming no patch queue process is running." >&2
      exit 2
    fi
    sleep 1
    waited=$((waited + 1))
  done
  trap 'rmdir "$LOCKDIR" >/dev/null 2>&1 || true' EXIT
}

prepare_worktree() {
  need_source
  mkdir -p "$(dirname "$WORK")" "$PATCH_DIR"
  if [[ ! -d "$WORK/.git" ]]; then
    git clone "$SOURCE" "$WORK"
  fi
  git -C "$WORK" config user.name "${GIT_AUTHOR_NAME:-Codex}"
  git -C "$WORK" config user.email "${GIT_AUTHOR_EMAIL:-codex@local}"
  git -C "$WORK" am --abort >/dev/null 2>&1 || true
  git -C "$WORK" fetch "$SOURCE" "$BASE"
  git -C "$WORK" checkout -B "$BRANCH" "$BASE"
  git -C "$WORK" reset --hard "$BASE"
  git -C "$WORK" clean -fdx
}

apply_queue() {
  prepare_worktree
  if compgen -G "$PATCH_DIR/*.patch" >/dev/null; then
    git -C "$WORK" am --3way "$PATCH_DIR"/*.patch
  fi
  if [[ -n "$EXTRA_PATCH_DIR" ]] && compgen -G "$EXTRA_PATCH_DIR/*.patch" >/dev/null; then
    git -C "$WORK" am --3way "$EXTRA_PATCH_DIR"/*.patch
  fi
}

format_queue() {
  if [[ ! -d "$WORK/.git" ]]; then
    echo "ERROR: patched worktree not found: $WORK" >&2
    echo "       Run: make patch-apply first" >&2
    exit 2
  fi
  mkdir -p "$PATCH_DIR"
  rm -f "$PATCH_DIR"/*.patch
  if git -C "$WORK" merge-base --is-ancestor "$BASE" HEAD && [[ "$(git -C "$WORK" rev-list --count "$BASE"..HEAD)" != "0" ]]; then
    git -C "$WORK" format-patch --output-directory "$PATCH_DIR" "$BASE"..HEAD
  else
    echo "No commits after base; patch queue is empty"
  fi
}

status_queue() {
  echo "Base: $BASE"
  echo "Pristine: $SOURCE"
  echo "Patched: $WORK"
  echo "Patch dir: $PATCH_DIR"
  if [[ -n "$EXTRA_PATCH_DIR" ]]; then
    echo "Extra patch dir: $EXTRA_PATCH_DIR"
  fi
  if [[ -d "$WORK/.git" ]]; then
    echo "Patched HEAD: $(git -C "$WORK" rev-parse --short HEAD)"
    echo "Commits after base: $(git -C "$WORK" rev-list --count "$BASE"..HEAD 2>/dev/null || echo unknown)"
    git -C "$WORK" status --short
  else
    echo "Patched worktree: missing"
  fi
  if compgen -G "$PATCH_DIR/*.patch" >/dev/null; then
    echo "Patch queue:"
    for patch in "$PATCH_DIR"/*.patch; do
      echo "  $(basename "$patch")"
    done
  else
    echo "Patch queue: empty"
  fi
  if [[ -n "$EXTRA_PATCH_DIR" ]] && compgen -G "$EXTRA_PATCH_DIR/*.patch" >/dev/null; then
    echo "Extra patch queue:"
    for patch in "$EXTRA_PATCH_DIR"/*.patch; do
      echo "  $(basename "$patch")"
    done
  fi
}

check_queue() {
  apply_queue
  if [[ -x "$LINUX/scripts/checkpatch.pl" ]] && compgen -G "$PATCH_DIR/*.patch" >/dev/null; then
    mkdir -p "$ROOT/artifacts"
    local log="$ROOT/artifacts/patch-check.log"
    : > "$log"
    local rc=0
    for patch in "$PATCH_DIR"/*.patch; do
      echo "Checking $(basename "$patch")" | tee -a "$log"
      if ! "$LINUX/scripts/checkpatch.pl" --no-tree "$patch" 2>&1 | tee -a "$log"; then
        rc=1
      fi
    done
    if [[ "$rc" -ne 0 && "${CHECKPATCH_STRICT:-0}" == "1" ]]; then
      exit "$rc"
    fi
    if [[ "$rc" -ne 0 ]]; then
      echo "WARNING: checkpatch reported issues; set CHECKPATCH_STRICT=1 to fail" >&2
    fi
  else
    echo "checkpatch skipped: $LINUX/scripts/checkpatch.pl not available or patch queue empty"
  fi
}

case "$ACTION" in
  prepare) acquire_lock; prepare_worktree ;;
  apply) acquire_lock; apply_queue ;;
  status) status_queue ;;
  format) acquire_lock; format_queue ;;
  check) acquire_lock; check_queue ;;
  *) usage; exit 2 ;;
esac
