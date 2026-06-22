from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from .common import sha256_file, write_json


DEFINE_RE = re.compile(r"^\s*#\s*define\s+(DRM_(?:COMMAND|IOCTL)_NVDLA_[A-Za-z0-9_]+)\b(.*)$")
STRUCT_RE = re.compile(r"^\s*struct\s+((?:drm_)?nvdla_[A-Za-z0-9_]+)\s*\{")


def _header_facts(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    defines = {}
    structs = []
    for line in text.splitlines():
        define = DEFINE_RE.match(line)
        if define:
            defines[define.group(1)] = " ".join(define.group(2).split())
        struct = STRUCT_RE.match(line)
        if struct:
            structs.append(struct.group(1))
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "defines": defines,
        "structs": sorted(structs),
    }


def check_abi(source: Path) -> dict[str, Any]:
    kmd = source / "kmd/port/linux/include/nvdla_ioctl.h"
    umd = source / "umd/port/linux/include/nvdla_ioctl.h"
    errors: list[str] = []
    if not kmd.exists():
        errors.append(f"KMD ioctl header missing: {kmd}")
    if not umd.exists():
        errors.append(f"UMD ioctl header missing: {umd}")
    if errors:
        return {"status": "fail", "errors": errors}

    kmd_facts = _header_facts(kmd)
    umd_facts = _header_facts(umd)
    warnings: list[str] = []
    if kmd_facts["sha256"] != umd_facts["sha256"]:
        warnings.append("KMD and UMD nvdla_ioctl.h hashes differ; semantic ABI facts still match")
    if kmd_facts["defines"] != umd_facts["defines"]:
        errors.append("KMD and UMD DRM ioctl define sets differ")
    if kmd_facts["structs"] != umd_facts["structs"]:
        errors.append("KMD and UMD drm_nvdla struct sets differ")

    return {
        "status": "fail" if errors else "pass",
        "errors": errors,
        "warnings": warnings,
        "kmd": kmd_facts,
        "umd": umd_facts,
    }


def run_abi_check(source: Path, out_path: Path | None) -> int:
    result = check_abi(source)
    if out_path:
        write_json(out_path, result)
    if result["status"] != "pass":
        for error in result.get("errors", []):
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    for warning in result.get("warnings", []):
        print(f"WARNING: {warning}", file=sys.stderr)
    print("ABI check passed")
    print(f"  header: {result['kmd']['path']}")
    return 0
