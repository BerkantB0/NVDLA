from __future__ import annotations

import json
import sys
from pathlib import Path


def _load_manifests(artifacts: Path) -> list[dict]:
    manifests = []
    if not artifacts.exists():
        return manifests
    for path in sorted(artifacts.glob("*/manifest.json")):
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            data["_path"] = str(path)
            manifests.append(data)
        except Exception as exc:
            manifests.append({"status": "unreadable", "_path": str(path), "error": str(exc)})
    return manifests


def write_report(artifacts: Path, out_path: Path) -> int:
    manifests = _load_manifests(artifacts)
    lines = ["# NVDLA Test Artifact Report", ""]
    if not manifests:
        lines.append("No artifact manifests found.")
    else:
        lines.append("| Run ID | Lane | Status | Manifest |")
        lines.append("| --- | --- | --- | --- |")
        for item in manifests:
            lines.append(
                f"| {item.get('run_id', 'unknown')} | {item.get('lane', 'unknown')} | {item.get('status', 'unknown')} | `{item['_path']}` |"
            )
    lines.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out_path}")
    if any(m.get("status") == "fail" for m in manifests):
        print("WARNING: one or more runs failed", file=sys.stderr)
    return 0

