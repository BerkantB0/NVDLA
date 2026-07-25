#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-status}"
POINTER="$ROOT/artifacts/latest-vp-resnet50-small-job.txt"
DEFAULT_WORK_DIR="${WORK_DIR:-$HOME/build/nvdla-peta/vp-modern}"

job_dir() {
    if [[ ! -f "$POINTER" ]]; then
        echo "no ResNet-50 VP background job has been started" >&2
        return 1
    fi
    cat "$POINTER"
}

job_pid() {
    local directory="$1"
    sed -n 's/^pid=//p' "$directory/job.env" | head -n 1
}

job_running() {
    local pid="$1"
    local command_line

    [[ -n "$pid" ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    [[ -r "/proc/$pid/cmdline" ]] || return 1
    command_line="$(tr '\000' ' ' <"/proc/$pid/cmdline")"
    [[ "$command_line" == *run_modern_lenet_full_control.sh* ]]
}

show_status() {
    local directory pid state
    directory="$(job_dir)"
    pid="$(job_pid "$directory")"
    state="finished"
    if job_running "$pid"; then
        state="running"
    fi

    echo "NVDLA_VP_JOB_STATE=$state"
    echo "NVDLA_VP_JOB_PID=$pid"
    echo "NVDLA_VP_JOB_DIR=$directory"
    if [[ -f "$directory/serial.log" ]]; then
        grep 'HWLs done, totally' "$directory/serial.log" | tail -n 1 \
            | sed 's/^/NVDLA_VP_PROGRESS=/' || true
    fi
    if [[ -f "$directory/manifest.json" ]]; then
        python3 - "$directory/manifest.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    manifest = json.load(source)
print(f"NVDLA_VP_RESULT={manifest.get('status')}")
print(f"NVDLA_VP_CLASSIFICATION={manifest.get('classification')}")
print(f"NVDLA_VP_OUTPUT_SHA256={manifest.get('output', {}).get('sha256')}")
PY
    elif [[ "$state" == "finished" ]]; then
        echo "NVDLA_VP_RESULT=incomplete"
    fi
    if [[ -f "$directory/background.log" ]]; then
        echo "--- background log tail ---"
        tail -n 12 "$directory/background.log"
    fi
}

case "$ACTION" in
    start)
        if [[ -f "$POINTER" ]]; then
            previous="$(cat "$POINTER")"
            if [[ -f "$previous/job.env" ]]; then
                previous_pid="$(job_pid "$previous")"
                if job_running "$previous_pid"; then
                    echo "a ResNet-50 VP job is already running: pid=$previous_pid dir=$previous" >&2
                    exit 1
                fi
            fi
        fi

        run_id="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-vp-modern-resnet50-small}"
        directory="$ROOT/artifacts/$run_id"
        mkdir -p "$directory"
        printf '%s\n' "$directory" >"$POINTER"

        nohup env \
            RUN_ID="$run_id" \
            WORKLOAD_KIND=resnet50 \
            VP_HW_CONFIG=small \
            VP_RUNNER=source-docker \
            VP_TIMEOUT="${VP_TIMEOUT:-604800}" \
            VP_TRACE=0 \
            VP_MODERN_DTB="${VP_MODERN_DTB:-$DEFAULT_WORK_DIR/dtb/nvdla-vp-modern-small-extmem-pool.dtb}" \
            RESNET50_DIR="${RESNET50_DIR:-$ROOT/artifacts/workloads/resnet50_small}" \
            "$ROOT/scripts/run_modern_lenet_full_control.sh" \
            >"$directory/background.log" 2>&1 </dev/null &
        pid=$!
        {
            echo "schema_version=1"
            echo "pid=$pid"
            echo "run_id=$run_id"
            echo "started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
            echo "timeout_seconds=${VP_TIMEOUT:-604800}"
            echo "git_revision=$(git -C "$ROOT" rev-parse HEAD)"
        } >"$directory/job.env"
        sleep 1
        if ! job_running "$pid"; then
            echo "ResNet-50 VP job exited during startup; inspect $directory/background.log" >&2
            show_status
            exit 1
        fi
        show_status
        ;;
    status)
        show_status
        ;;
    *)
        echo "usage: $0 {start|status}" >&2
        exit 2
        ;;
esac
