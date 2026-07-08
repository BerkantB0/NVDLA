from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .common import read_json, run_command, sha256_file, utc_run_id, write_json


def _path_hash(path: Path) -> str | None:
    return sha256_file(path) if path.is_file() else None


def _parse_cmake_cache(path: Path) -> dict[str, str]:
    result = {}
    if not path.is_file():
        return result
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.split(":", 1)[0]] = value
    return result


def _latest_probe_config(artifacts: Path) -> dict[str, Any]:
    candidates = []
    for manifest_path in sorted(artifacts.glob("*/manifest.json")):
        try:
            manifest = read_json(manifest_path)
        except Exception:
            continue
        modern = manifest.get("modern") if isinstance(manifest.get("modern"), dict) else manifest
        probe = modern.get("probe_config") or manifest.get("probe_config")
        if probe:
            candidates.append(
                {
                    "probe_config": probe,
                    "manifest": str(manifest_path),
                    "run_id": manifest.get("run_id") or modern.get("run_id"),
                    "status": manifest.get("status") or modern.get("status"),
                }
            )
    small = [item for item in candidates if item.get("probe_config") == "nvidia,nv_small"]
    return (small or candidates)[-1] if candidates else {"probe_config": None}


def run_vp_small_config_audit(lock_path: Path, work_dir: Path, artifacts: Path) -> int:
    lock = read_json(lock_path)
    image = lock["docker"]["vp_latest"]["image"]
    run_id = utc_run_id("vp-small-config-audit")
    out_dir = artifacts / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    vp_root = work_dir / "vp-small"
    vp_binary = vp_root / "install" / "bin" / "aarch64_toplevel"
    vp_lib = vp_root / "install" / "lib"
    cmod_lib = vp_root / "hw" / "outdir" / "nv_small" / "cmod" / "release" / "lib"
    cmod = cmod_lib / "libnvdla_cmod.so"
    installed_cmod = vp_lib / "libnvdla_cmod.so"
    cmake_cache = vp_root / "vp-build" / "CMakeCache.txt"
    dtb = work_dir / "dtb" / "nvdla-vp-modern-small-extmem-pool.dtb"
    module = work_dir / "modules" / "opendla.ko"
    cache = _parse_cmake_cache(cmake_cache)

    ldd_log = out_dir / "ldd-aarch64_toplevel.log"
    ldd_cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{vp_binary.parent.resolve()}:/vp-small-bin:ro",
        "-v",
        f"{vp_lib.resolve()}:/vp-small-lib:ro",
        "-v",
        f"{cmod_lib.resolve()}:/vp-small-cmod:ro",
        image,
        "bash",
        "-lc",
        (
            "export LD_LIBRARY_PATH=/vp-small-lib:/vp-small-cmod:"
            "/usr/local/systemc-2.3.0/lib-linux64:${LD_LIBRARY_PATH:-}; "
            "ldd /vp-small-bin/aarch64_toplevel"
        ),
    ]
    missing = [
        str(path)
        for path in [vp_binary, vp_lib, cmod_lib, cmod, installed_cmod, cmake_cache, dtb, module]
        if not path.exists()
    ]
    ldd_status = None
    ldd_text = ""
    ldd_error = None
    if not missing:
        try:
            cp = run_command(ldd_cmd, timeout=60)
            ldd_status = cp.returncode
            ldd_text = cp.stdout
        except OSError as exc:
            ldd_status = 127
            ldd_error = str(exc)
            ldd_text = ldd_error
        ldd_log.write_text(ldd_text, encoding="utf-8", errors="replace")

    probe = _latest_probe_config(artifacts)
    cmod_match = bool(re.search(r"libnvdla_cmod\.so\s+=>\s+/(vp-small-cmod|vp-small-lib)/", ldd_text))
    hw_project = cache.get("NVDLA_HW_PROJECT")
    raw_cmod_hash = _path_hash(cmod)
    installed_cmod_hash = _path_hash(installed_cmod)
    checks = {
        "cmake_hw_project_is_nv_small": hw_project == "nv_small",
        "ldd_uses_mounted_nv_small_cmod": cmod_match,
        "installed_cmod_matches_nv_small_build": installed_cmod_hash == raw_cmod_hash,
        "probe_config_is_nv_small": probe.get("probe_config") == "nvidia,nv_small",
        "required_paths_exist": not missing,
    }
    status = "pass" if all(checks.values()) and ldd_status == 0 else "blocked" if missing else "fail"
    reason = None
    if status != "pass":
        failed = [name for name, value in checks.items() if not value]
        reason = ", ".join(failed) if failed else f"ldd failed with status {ldd_status}"

    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "lane": "vp-small",
        "mode": "config_audit",
        "status": status,
        "reason": reason,
        "checks": checks,
        "paths": {
            "work_dir": str(work_dir),
            "vp_binary": str(vp_binary),
            "vp_library_dir": str(vp_lib),
            "vp_cmod_library_dir": str(cmod_lib),
            "vp_installed_cmod": str(installed_cmod),
            "cmake_cache": str(cmake_cache),
            "dtb": str(dtb),
            "module": str(module),
        },
        "hashes": {
            "vp_binary": _path_hash(vp_binary),
            "vp_cmod": _path_hash(cmod),
            "cmake_cache": _path_hash(cmake_cache),
            "dtb": _path_hash(dtb),
            "module": _path_hash(module),
            "vp_installed_cmod": installed_cmod_hash,
        },
        "cmake": {
            "NVDLA_HW_PROJECT": hw_project,
        },
        "ldd": {
            "status": ldd_status,
            "log": "ldd-aarch64_toplevel.log" if ldd_log.is_file() else None,
            "cmod_match": cmod_match,
            "error": ldd_error,
        },
        "probe": probe,
        "missing": missing,
    }
    write_json(out_dir / "manifest.json", manifest)
    print(f"VP small config audit status: {status}")
    print(f"Artifacts: {out_dir}")
    return 0 if status == "pass" else 1
