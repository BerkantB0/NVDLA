#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT/tools:${PYTHONPATH:-}"
PETALINUX_DIR="${PETALINUX_DIR:-/opt/pkg/petalinux/2024.1}"
PETALINUX_PROJECT="${PETALINUX_PROJECT:-$HOME/build/nvdla-peta/petalinux/zcu102-nvdla}"
ARTIFACTS="${ARTIFACTS_DIR:-$ROOT/artifacts}"
XSA_PATH="${XSA_PATH:-$ROOT/NVDLA_FPGA_wrapper.xsa}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

pl_start_run() {
  PL_PHASE="$1"
  RUN_ID="${RUN_ID:-$STAMP-petalinux-$PL_PHASE}"
  RUN_DIR="$ARTIFACTS/$RUN_ID"
  BUILD_LOG="$RUN_DIR/petalinux-$PL_PHASE.log"
  SETTINGS_LOG="$RUN_DIR/petalinux-settings.log"
  mkdir -p "$RUN_DIR"
  export PL_PHASE RUN_ID RUN_DIR BUILD_LOG SETTINGS_LOG
}

pl_source_settings() {
  if [[ ! -f "$PETALINUX_DIR/settings.sh" ]]; then
    echo "ERROR: PetaLinux settings not found at $PETALINUX_DIR/settings.sh" >&2
    return 1
  fi
  set +e +u
  # shellcheck disable=SC1091
  source "$PETALINUX_DIR/settings.sh" >"$SETTINGS_LOG" 2>&1
  local status=$?
  set -euo pipefail
  if [[ "$status" -ne 0 ]]; then
    echo "WARNING: PetaLinux settings returned $status; verifying tool environment" | tee -a "$SETTINGS_LOG" >&2
  fi
  for tool in petalinux-build petalinux-config petalinux-create petalinux-package; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      echo "ERROR: $tool not found after sourcing PetaLinux settings" | tee -a "$SETTINGS_LOG" >&2
      return 1
    fi
  done
  return 0
}

pl_patch_series_sha() {
  python3 - <<'PY'
import hashlib
from pathlib import Path

root = Path.cwd()
patches = sorted((root / "patches" / "nvdla-sw").glob("*.patch"))
digest = hashlib.sha256()
for patch in patches:
    digest.update(patch.name.encode("utf-8"))
    digest.update(b"\0")
    digest.update(patch.read_bytes())
print(digest.hexdigest() if patches else "")
PY
}

pl_kernel_version() {
  local header
  header="$(find "$PETALINUX_PROJECT/build" -path '*/include/generated/utsrelease.h' -type f 2>/dev/null | sort | head -n 1 || true)"
  if [[ -n "$header" ]]; then
    sed -n 's/^#define UTS_RELEASE "\(.*\)"/\1/p' "$header" | head -n 1
  fi
}

pl_module_vermagic() {
  local module="$1"
  if [[ ! -f "$module" ]]; then
    return 0
  fi
  if command -v modinfo >/dev/null 2>&1; then
    modinfo -F vermagic "$module" 2>/dev/null && return 0
  fi
  strings "$module" 2>/dev/null | sed -n 's/^vermagic=//p' | head -n 1
}

pl_write_manifest() {
  local status="$1"
  local reason="${2:-}"
  export STATUS="$status"
  export REASON="$reason"
  export ROOT PETALINUX_DIR PETALINUX_PROJECT XSA_PATH
  export PATCH_SERIES_SHA="${PATCH_SERIES_SHA:-$(pl_patch_series_sha)}"
  export KERNEL_VERSION="${KERNEL_VERSION:-$(pl_kernel_version)}"
  python3 - <<'PY'
import hashlib
import json
import os
import platform
from pathlib import Path


def sha256(path: str | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    digest = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def os_release() -> dict[str, str]:
    path = Path("/etc/os-release")
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key] = value.strip().strip('"')
    return data


run_dir = Path(os.environ["RUN_DIR"])
logs = sorted(path.name for path in run_dir.glob("*.log"))
json_files = sorted(path.name for path in run_dir.glob("*.json") if path.name != "manifest.json")
module_path = os.environ.get("MODULE_PATH") or None
dts_path = os.environ.get("DTS_PATH") or None
package_path = os.environ.get("PACKAGE_PATH") or None
image_dir = Path(os.environ["PETALINUX_PROJECT"]) / "images" / "linux"
image_files = {}
if image_dir.is_dir():
    for name in ("image.ub", "system.dtb", "boot.scr", "rootfs.cpio.gz.u-boot", "BOOT.BIN"):
        path = image_dir / name
        if path.is_file():
            image_files[name] = {"path": str(path), "sha256": sha256(str(path))}

manifest = {
    "schema_version": 1,
    "run_id": os.environ["RUN_ID"],
    "lane": f"petalinux-{os.environ['PL_PHASE']}",
    "phase": os.environ["PL_PHASE"],
    "status": os.environ["STATUS"],
    "reason": os.environ.get("REASON") or None,
    "host": {
        "wsl_distro": os.environ.get("WSL_DISTRO_NAME"),
        "os_release": os_release(),
        "python": platform.python_version(),
        "machine": platform.machine(),
    },
    "petalinux": {
        "install_dir": os.environ["PETALINUX_DIR"],
        "project": os.environ["PETALINUX_PROJECT"],
        "settings_log": os.environ["SETTINGS_LOG"],
        "unsupported_host_warning": "not a supported os"
        in Path(os.environ["SETTINGS_LOG"]).read_text(encoding="utf-8", errors="replace").lower()
        if Path(os.environ["SETTINGS_LOG"]).exists()
        else None,
    },
    "sources": {
        "xsa_path": os.environ["XSA_PATH"],
        "xsa_sha256": sha256(os.environ["XSA_PATH"]),
        "nvdla_patch_series_sha256": os.environ.get("PATCH_SERIES_SHA") or None,
    },
    "kernel": {
        "version": os.environ.get("KERNEL_VERSION") or None,
    },
    "driver": {
        "kmd_config": os.environ.get("NVDLA_KMD_CONFIG") or os.environ.get("NVDLA_HW_CONFIG") or None,
        "module_path": module_path,
        "module_sha256": sha256(module_path),
        "module_vermagic": os.environ.get("MODULE_VERMAGIC") or None,
    },
    "device_tree": {
        "fragment_path": dts_path,
        "fragment_sha256": sha256(dts_path),
        "audit_path": os.environ.get("DTS_AUDIT_PATH") or None,
    },
    "images": image_files,
    "package": {
        "boot_bin": package_path,
        "boot_bin_sha256": sha256(package_path),
    },
    "recipe_files": os.environ.get("RECIPE_FILES", "").split(":") if os.environ.get("RECIPE_FILES") else [],
    "logs": logs,
    "json": json_files,
}
(run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"Wrote {run_dir / 'manifest.json'}")
PY
}

pl_finish_blocked() {
  local reason="$1"
  echo "BLOCKED: $reason" | tee -a "$BUILD_LOG" >&2
  pl_write_manifest "blocked" "$reason"
  exit 2
}

pl_finish_fail() {
  local reason="$1"
  echo "ERROR: $reason" | tee -a "$BUILD_LOG" >&2
  pl_write_manifest "fail" "$reason"
  exit 1
}

pl_require_project() {
  if [[ ! -d "$PETALINUX_PROJECT/project-spec/meta-user" ]]; then
    pl_finish_blocked "PETALINUX_PROJECT is not a configured PetaLinux project: $PETALINUX_PROJECT"
  fi
}
