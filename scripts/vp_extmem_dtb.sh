#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="${WORK_DIR:-$ROOT/.work/vp-modern}"
VP_HW_CONFIG="${VP_HW_CONFIG:-full}"

case "$VP_HW_CONFIG" in
    full)
        DEFAULT_DTS="$ROOT/configs/vp/nvdla-vp-modern-extmem-pool.dts"
        DEFAULT_OUT="$WORK/dtb/nvdla-vp-modern-extmem-pool.dtb"
        ;;
    small)
        DEFAULT_DTS="$ROOT/configs/vp/nvdla-vp-modern-small-extmem-pool.dts"
        DEFAULT_OUT="$WORK/dtb/nvdla-vp-modern-small-extmem-pool.dtb"
        ;;
    *)
        echo "ERROR: unsupported VP_HW_CONFIG=$VP_HW_CONFIG; expected full or small" >&2
        exit 2
        ;;
esac

DTS="${VP_EXTMEM_DTS:-$DEFAULT_DTS}"
OUT="${VP_EXTMEM_DTB:-$DEFAULT_OUT}"

if [[ ! -f "$DTS" ]]; then
    echo "ERROR: DTS not found: $DTS" >&2
    exit 2
fi

DTC_BIN="${DTC:-}"
if [[ -z "$DTC_BIN" ]]; then
    if [[ -x "$WORK/kernel/scripts/dtc/dtc" ]]; then
        DTC_BIN="$WORK/kernel/scripts/dtc/dtc"
    elif command -v dtc >/dev/null 2>&1; then
        DTC_BIN="$(command -v dtc)"
    else
        echo "ERROR: dtc not found. Build vp-kernel first or set DTC=/path/to/dtc." >&2
        exit 2
    fi
fi

mkdir -p "$(dirname "$OUT")"
"$DTC_BIN" -I dts -O dtb -o "$OUT" "$DTS"

if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$OUT" >"$OUT.sha256"
fi

echo "Wrote $OUT"
