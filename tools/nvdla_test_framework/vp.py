from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

from .common import read_json, run_command, sha256_file, utc_run_id, write_json
from .patches import patch_series_fingerprint


KERNEL_BAD_PATTERNS = [
    r"\bOops\b",
    r"\bBUG:",
    r"\bWARNING:",
    r"DMA-API",
    r"scheduler timeout",
    r"interrupt timeout",
]


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="replace")


def _stock_vp_boot(lock: dict[str, Any], timeout: int, out_dir: Path) -> dict[str, Any]:
    image = lock["docker"]["vp_latest"]["image"]
    command = [
        "docker",
        "run",
        "--rm",
        image,
        "bash",
        "-lc",
        f"cd /usr/local/nvdla && timeout {timeout}s aarch64_toplevel -c aarch64_nvdla.lua",
    ]
    cp = run_command(command, timeout=timeout + 10)
    log = cp.stdout
    _write_text(out_dir / "serial.log", log)

    reached_login = "Welcome to Buildroot" in log and "nvdla login:" in log
    bad = [pat for pat in KERNEL_BAD_PATTERNS if re.search(pat, log, flags=re.IGNORECASE)]
    status = "pass" if reached_login and not bad and cp.returncode in {0, 124} else "fail"
    return {
        "status": status,
        "returncode": cp.returncode,
        "reached_login": reached_login,
        "bad_patterns": bad,
        "serial_log": "serial.log",
    }


def _compiler_smoke(lock: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    image = lock["docker"]["vp_latest"]["image"]
    cp = run_command(
        [
            "docker",
            "run",
            "--rm",
            image,
            "bash",
            "-lc",
            "LD_LIBRARY_PATH=/usr/local/nvdla /usr/local/nvdla/nvdla_compiler -h",
        ],
        timeout=20,
    )
    _write_text(out_dir / "compiler.stdout.log", cp.stdout)
    ok = cp.returncode == 0 and "--configtarget <nv_full|nv_large|nv_small>" in cp.stdout
    return {
        "status": "pass" if ok else "fail",
        "returncode": cp.returncode,
        "log": "compiler.stdout.log",
    }


def _modern_lane_probe(out_dir: Path) -> dict[str, Any]:
    required = {
        "VP_MODERN_KERNEL": os.environ.get("VP_MODERN_KERNEL"),
        "VP_MODERN_ROOTFS": os.environ.get("VP_MODERN_ROOTFS"),
        "VP_MODERN_KO": os.environ.get("VP_MODERN_KO"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        return {
            "status": "blocked",
            "reason": "modern VP artifacts are not configured",
            "missing_environment": missing,
        }
    hashes = {}
    for name, value in required.items():
        path = Path(value)
        if not path.exists():
            return {"status": "fail", "reason": f"{name} path does not exist: {path}"}
        hashes[name.lower()] = sha256_file(path)
    _write_text(
        out_dir / "modern-lane.todo.txt",
        "Modern VP artifact paths are present. Full boot/login/module/runtime automation is the next implementation step.\n",
    )
    return {"status": "blocked", "reason": "modern VP runner not wired yet", "artifact_hashes": hashes}


def run_vp_test(lane: str, lock_path: Path, timeout: int, out_dir: Path | None) -> int:
    lock = read_json(lock_path)
    run_id = utc_run_id(f"vp-{lane}")
    out = out_dir or Path("artifacts") / run_id
    out.mkdir(parents=True, exist_ok=True)

    if lane == "reference":
        boot = _stock_vp_boot(lock, timeout, out)
        compiler = _compiler_smoke(lock, out)
        status = "pass" if boot["status"] == "pass" and compiler["status"] == "pass" else "fail"
        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "lane": "vp-reference",
            "status": status,
            "boot": boot,
            "compiler": compiler,
            "sources": {"nvdla_sw": lock["sources"]["nvdla_sw"]["commit"]},
            "patch_series": patch_series_fingerprint(),
            "docker": lock["docker"]["vp_latest"],
            "workloads": [],
        }
    else:
        probe = _modern_lane_probe(out)
        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "lane": "vp-modern",
            "status": probe["status"],
            "probe": probe,
            "sources": {
                "nvdla_sw": lock["sources"]["nvdla_sw"]["commit"],
                "linux_xlnx": lock["sources"]["linux_xlnx"]["commit"],
            },
            "patch_series": patch_series_fingerprint(),
            "workloads": [],
        }

    write_json(out / "manifest.json", manifest)
    print(f"VP {lane} status: {manifest['status']}")
    print(f"Artifacts: {out}")
    return 0 if manifest["status"] == "pass" else 1
