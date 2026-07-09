from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


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


def _modern(item: dict[str, Any]) -> dict[str, Any]:
    return item.get("modern") if isinstance(item.get("modern"), dict) else item


def _latest_matching(manifests: list[dict], predicate: Any) -> dict | None:
    matches = [item for item in manifests if predicate(item)]
    return matches[-1] if matches else None


def _small_highlights(manifests: list[dict], artifacts: Path) -> list[str]:
    lines = ["## nv_small Highlights", ""]
    smoke = _latest_matching(
        manifests,
        lambda item: _modern(item).get("vp_hw_config") == "small" and _modern(item).get("mode") == "smoke",
    )
    lenet = _latest_matching(
        manifests,
        lambda item: item.get("mode") == "lenet_small_control"
        or _modern(item).get("mode") == "lenet_small_control",
    )
    stability = _latest_matching(
        manifests,
        lambda item: (item.get("mode") == "lenet_small_control" or _modern(item).get("mode") == "lenet_small_control")
        and int(item.get("repeat_count") or _modern(item).get("repeat_count") or 1) >= 100,
    )
    sdp = _latest_matching(
        manifests,
        lambda item: _modern(item).get("mode") == "runtime"
        and any(w.get("name") == "sdp_regression_small" for w in (_modern(item).get("workloads") or [])),
    )

    for label, item in [
        ("Smoke", smoke),
        ("LeNet Gate", lenet),
        ("LeNet Stability", stability),
        ("SDP Diagnostic", sdp),
    ]:
        if item is None:
            lines.append(f"- {label}: not found")
            continue
        modern = _modern(item)
        manifest_path = item.get("_path", "")
        status = item.get("status") or modern.get("status")
        extra = ""
        if label.startswith("LeNet"):
            extra = f", repeat={item.get('repeat_count') or modern.get('repeat_count') or 1}"
        if label == "SDP Diagnostic":
            diag = Path(manifest_path).parent / "sdp-small-diagnostic.json"
            if diag.is_file():
                try:
                    diag_data = json.loads(diag.read_text(encoding="utf-8"))
                    status = f"manifest={status}"
                    extra = (
                        f", diagnostic={diag_data.get('status')}, "
                        f"classification={diag_data.get('classification')}"
                    )
                except Exception:
                    extra = ", classification=unreadable"
        lines.append(f"- {label}: {status}{extra} (`{manifest_path}`)")
    lines.append("")
    return lines


def write_report(artifacts: Path, out_path: Path) -> int:
    manifests = _load_manifests(artifacts)
    lines = ["# NVDLA Test Artifact Report", ""]
    if not manifests:
        lines.append("No artifact manifests found.")
    else:
        lines.extend(_small_highlights(manifests, artifacts))
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
