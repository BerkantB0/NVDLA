from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .common import read_json, sha256_file, write_json
from .vp import _bad_patterns


DEFAULT_STOCK_DIR = Path("artifacts/20260703T115149Z-vp-stock-lenet")


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _normalize_output(text: str) -> str:
    return " ".join(text.split())


def _latest_modern_artifact(root: Path) -> Path:
    candidates = sorted(root.glob("*-vp-modern-lenet-full"))
    if not candidates:
        raise FileNotFoundError(f"no modern LeNet artifacts under {root}")
    return candidates[-1]


def _completed_layers(text: str) -> list[dict[str, Any]]:
    layers = []
    pattern = re.compile(r"Completed\s+(\w+)\s+operation index\s+(\d+)\s+ROI\s+(\d+)")
    for match in pattern.finditer(text):
        layers.append(
            {
                "processor": match.group(1),
                "index": int(match.group(2)),
                "roi": int(match.group(3)),
            }
        )
    return layers


def _hwl_progress(text: str) -> dict[str, int | None]:
    matches = re.findall(r"(\d+)\s+HWLs done,\s+totally\s+(\d+)\s+layers", text)
    if not matches:
        return {"done": None, "total": None}
    done, total = matches[-1]
    return {"done": int(done), "total": int(total)}


def _trace_summary(text: str) -> dict[str, Any]:
    tags: dict[str, int] = {}
    hashes: list[dict[str, Any]] = []
    pattern = re.compile(
        r"nvdla-trace\s+(\S+)\s+index=(\d+)\s+fd=(\d+).*?"
        r"hash=0x([0-9a-fA-F]+)\s+first=([0-9a-fA-F]*)"
    )
    for match in pattern.finditer(text):
        tag = match.group(1)
        tags[tag] = tags.get(tag, 0) + 1
        hashes.append(
            {
                "tag": tag,
                "index": int(match.group(2)),
                "fd": int(match.group(3)),
                "hash": match.group(4).lower(),
                "first": match.group(5).lower(),
            }
        )
    return {
        "line_count": sum(tags.values()),
        "tags": tags,
        "hashes": hashes,
    }


def _bad_pattern_lines(path: Path) -> list[str]:
    return [line.strip() for line in _read_text(path).splitlines() if line.strip()]


def _stock_summary(stock_dir: Path) -> dict[str, Any]:
    output = stock_dir / "output-nvfull-visible.txt"
    dmesg = stock_dir / "dmesg-nvfull-visible.log"
    serial_text = _read_text(dmesg)
    return {
        "dir": str(stock_dir),
        "output": _normalize_output(_read_text(output)),
        "output_sha256": sha256_file(output) if output.is_file() else None,
        "completed_layers": _completed_layers(serial_text),
        "hwl_progress": _hwl_progress(serial_text),
        "bad_patterns": _bad_patterns(serial_text),
        "trace": _trace_summary(serial_text),
        "log": str(dmesg),
    }


def _modern_summary(modern_dir: Path) -> dict[str, Any]:
    manifest_path = modern_dir / "manifest.json"
    manifest = read_json(manifest_path) if manifest_path.is_file() else {}
    output_path = modern_dir / "runtime-output" / "output.txt"
    serial = modern_dir / "serial.log"
    dmesg = modern_dir / "dmesg.log"
    bad_patterns = modern_dir / "bad-patterns.log"
    serial_text = _read_text(serial)
    dmesg_text = _read_text(dmesg)
    analysis_text = dmesg_text or serial_text
    output = manifest.get("actual_output") or _normalize_output(_read_text(output_path))
    expected = manifest.get("expected_output")
    bad_pattern_hits = sorted(set(_bad_patterns(analysis_text) + _bad_pattern_lines(bad_patterns)))
    return {
        "dir": str(modern_dir),
        "status": manifest.get("status"),
        "expected_output": expected,
        "actual_output": _normalize_output(output),
        "output_sha256": sha256_file(output_path) if output_path.is_file() else None,
        "completed_layers": _completed_layers(analysis_text),
        "hwl_progress": _hwl_progress(analysis_text),
        "bad_patterns": bad_pattern_hits,
        "trace": _trace_summary(analysis_text),
        "manifest": str(manifest_path),
        "module_sha256": ((manifest.get("inputs") or {}).get("module") or {}).get("sha256"),
        "runtime_sha256": ((manifest.get("inputs") or {}).get("runtime") or {}).get("sha256"),
        "loadable_sha256": ((manifest.get("inputs") or {}).get("loadable") or {}).get("sha256"),
        "vp_ram": manifest.get("vp_ram"),
    }


def _classify(stock: dict[str, Any], modern: dict[str, Any]) -> str:
    if modern["bad_patterns"]:
        return "kernel_log_bad_pattern"
    if not modern["actual_output"]:
        return "missing_modern_output"
    if modern["actual_output"] == stock["output"]:
        return "pass"
    progress = modern.get("hwl_progress") or {}
    if progress.get("done") == progress.get("total") and progress.get("done"):
        return "runtime_clean_output_mismatch"
    if modern["completed_layers"]:
        return "partial_execution_output_mismatch"
    return "inconclusive_output_mismatch"


def compare_lenet_control(stock_dir: Path, modern_dir: Path | None, out: Path | None) -> int:
    stock_dir = stock_dir.resolve()
    modern_dir = (modern_dir.resolve() if modern_dir else _latest_modern_artifact(Path("artifacts"))).resolve()
    stock = _stock_summary(stock_dir)
    modern = _modern_summary(modern_dir)
    output_match = bool(stock["output"]) and stock["output"] == modern["actual_output"]
    layer_sequence_match = stock["completed_layers"] == modern["completed_layers"]
    result = {
        "schema_version": 1,
        "status": "pass" if output_match and not modern["bad_patterns"] else "fail",
        "classification": _classify(stock, modern),
        "stock": stock,
        "modern": modern,
        "comparisons": {
            "output_match": output_match,
            "layer_sequence_match": layer_sequence_match,
            "stock_layer_count": len(stock["completed_layers"]),
            "modern_layer_count": len(modern["completed_layers"]),
        },
    }
    out_path = out or (modern_dir / "lenet-compare.json")
    write_json(out_path, result)
    print(f"LeNet comparison status: {result['status']}")
    print(f"Classification: {result['classification']}")
    print(f"Comparison: {out_path}")
    return 0 if result["status"] == "pass" else 1
