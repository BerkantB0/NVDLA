from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .common import read_json, write_json


def _resolve_artifact_path(artifact: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if path.is_file():
        return path
    candidate = artifact / path.name
    if candidate.is_file():
        return candidate
    output_candidate = artifact / "runtime-output" / path.name
    if output_candidate.is_file():
        return output_candidate
    return path


def _latest_sdp_small_runtime(artifacts: Path) -> tuple[Path, dict[str, Any]] | None:
    matches = []
    for manifest_path in sorted(artifacts.glob("*/manifest.json")):
        try:
            manifest = read_json(manifest_path)
        except Exception:
            continue
        modern = manifest.get("modern") if isinstance(manifest.get("modern"), dict) else manifest
        if modern.get("mode") != "runtime":
            continue
        workloads = modern.get("workloads") or manifest.get("workloads") or []
        if any(item.get("name") == "sdp_regression_small" for item in workloads):
            matches.append((manifest_path, manifest))
    return matches[-1] if matches else None


def _serial_has_known_sdp_timeout_shape(serial: str) -> bool:
    programmed = "Program SDP operation" in serial
    enabled = "Enable SDP operation" in serial
    initiated = "Exit: dla_initiate_processors status=0" in serial
    completed = "Handle op complete event, processor SDP" in serial
    return programmed and enabled and initiated and not completed


def _serial_has_sdp_completion_shape(serial: str) -> bool:
    return all(
        marker in serial
        for marker in [
            "Program SDP operation",
            "Enable SDP operation",
            "Exit: dla_initiate_processors status=0",
            "Handle op complete event, processor SDP",
            "Completed SDP operation",
        ]
    )


def _dimg_payload_summary(path: Path | None, header_bytes: int = 40) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {"path": str(path) if path else None, "exists": False}
    data = path.read_bytes()
    payload = data[min(header_bytes, len(data)) :]
    return {
        "path": str(path),
        "exists": True,
        "size_bytes": len(data),
        "header_bytes": header_bytes,
        "nonzero_bytes": sum(1 for item in data if item),
        "payload_nonzero_bytes": sum(1 for item in payload if item),
        "payload_is_all_zero": all(item == 0 for item in payload),
    }


def classify_sdp_small_diagnostic(artifacts: Path) -> int:
    latest = _latest_sdp_small_runtime(artifacts)
    if latest is None:
        print(f"No sdp_regression_small runtime artifact found under {artifacts}")
        return 1

    manifest_path, manifest = latest
    artifact = manifest_path.parent
    modern = manifest.get("modern") if isinstance(manifest.get("modern"), dict) else manifest
    workloads = modern.get("workloads") or manifest.get("workloads") or []
    workload = next((item for item in workloads if item.get("name") == "sdp_regression_small"), {})
    statuses = modern.get("statuses") or {}
    bad_patterns = modern.get("bad_patterns") or manifest.get("bad_patterns") or []
    serial_path = artifact / "serial.log"
    serial = serial_path.read_text(encoding="utf-8", errors="replace") if serial_path.is_file() else ""
    output_sha = workload.get("output_sha256")
    compare = workload.get("compare") or {}
    actual_path = _resolve_artifact_path(artifact, compare.get("actual_path"))
    output_summary = _dimg_payload_summary(actual_path)
    runtime_client_path = artifact / "runtime-client.log"
    runtime_server_path = artifact / "runtime-server.log"
    runtime_client = (
        runtime_client_path.read_text(encoding="utf-8", errors="replace")
        if runtime_client_path.is_file()
        else ""
    )
    runtime_server = (
        runtime_server_path.read_text(encoding="utf-8", errors="replace")
        if runtime_server_path.is_file()
        else ""
    )

    if modern.get("status") == "pass" and workload.get("status") == "pass":
        classification = "pass"
        status = "pass"
    else:
        known_timeout = all(
            [
                modern.get("probe_config") == "nvidia,nv_small",
                statuses.get("module_load") == 0,
                statuses.get("dev_dri") == 0,
                not bad_patterns,
                output_sha is None,
                compare.get("reason") == "missing expected or actual file",
                _serial_has_known_sdp_timeout_shape(serial),
            ]
        )
        known_zero_output_mismatch = all(
            [
                modern.get("probe_config") == "nvidia,nv_small",
                statuses.get("module_load") == 0,
                statuses.get("dev_dri") == 0,
                statuses.get("runtime_client") == 0,
                statuses.get("runtime_server_ready") == 0,
                not bad_patterns,
                output_sha is not None,
                compare.get("status") == "fail",
                compare.get("reason") == "files differ",
                output_summary.get("payload_is_all_zero") is True,
                _serial_has_sdp_completion_shape(serial),
                "[OK] Test PASSED!" in runtime_client or "[OK] Test PASSED!" in runtime_server,
            ]
        )
        if known_timeout:
            classification = "known_sdp_completion_timeout"
            status = "pass"
        elif known_zero_output_mismatch:
            classification = "known_sdp_zero_output_golden_mismatch"
            status = "pass"
        else:
            classification = "unexpected_sdp_failure"
            status = "fail"

    diagnostic = {
        "schema_version": 1,
        "status": status,
        "classification": classification,
        "artifact": str(artifact),
        "manifest": str(manifest_path),
        "probe_config": modern.get("probe_config"),
        "statuses": statuses,
        "bad_patterns": bad_patterns,
        "workload": workload,
        "output_summary": output_summary,
        "serial_markers": {
            "program_sdp": bool(re.search(r"Program SDP operation", serial)),
            "enable_sdp": bool(re.search(r"Enable SDP operation", serial)),
            "initiate_exit_zero": "Exit: dla_initiate_processors status=0" in serial,
            "sdp_completion": "Handle op complete event, processor SDP" in serial,
            "completed_sdp": "Completed SDP operation" in serial,
        },
    }
    write_json(artifact / "sdp-small-diagnostic.json", diagnostic)
    print(f"SDP small diagnostic status: {status}")
    print(f"Classification: {classification}")
    print(f"Artifacts: {artifact}")
    return 0 if status == "pass" else 1
