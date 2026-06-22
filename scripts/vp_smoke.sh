#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT/tools:${PYTHONPATH:-}"

LANE="${1:-reference}"
TIMEOUT="${VP_TIMEOUT:-35}"

python3 -m nvdla_test_framework vp-test --lane "$LANE" --lock repro.lock.json --timeout "$TIMEOUT"
