from __future__ import annotations

import re
import shutil
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any

from .board_payload import EXPECTED_LENET_OPERATIONS, EXPECTED_LENET_OUTPUT
from .common import sha256_file, write_json


def _parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _read_int(path: Path, default: int | None = None) -> int | None:
    try:
        return int(_read_text(path).strip())
    except (TypeError, ValueError):
        return default


def parse_interrupt_total(text: str, device: str = "nvdla") -> int | None:
    total = 0
    matched = False
    for line in text.splitlines():
        if device.lower() not in line.lower() or ":" not in line:
            continue
        matched = True
        for field in line.split(":", 1)[1].split():
            if not field.isdigit():
                break
            total += int(field)
    return total if matched else None


def parse_completed_operations(text: str) -> list[dict[str, int | str]]:
    return [
        {
            "processor": match.group(1),
            "index": int(match.group(2)),
        }
        for match in re.finditer(
            r"Completed\s+([A-Za-z]+)\s+operation index\s+(\d+)\s+ROI",
            text,
        )
    ]


def _normal_output(text: str) -> str:
    return " ".join(text.split())


def _irq_evidence(root: Path, before_name: str, after_name: str) -> dict[str, Any]:
    before = parse_interrupt_total(_read_text(root / before_name))
    after = parse_interrupt_total(_read_text(root / after_name))
    delta = after - before if before is not None and after is not None else None
    return {"before": before, "after": after, "delta": delta}


def _classify_lenet_repeat(path: Path, index: int) -> dict[str, Any]:
    result_env = _parse_env(path / "result.env") if (path / "result.env").is_file() else {}
    runtime_status = _read_int(path / "runtime.exit-status")
    dmesg = _read_text(path / "dmesg-delta.log")
    operations = parse_completed_operations(dmesg)
    expected = EXPECTED_LENET_OPERATIONS
    actual_output = _normal_output(_read_text(path / "output.txt"))
    output_path = path / "output.dimg"
    irq_delta = _read_int(path / "irq-delta.txt")
    initiated = "Exit: dla_initiate_processors status=0" in dmesg
    timed_out = (path / "runtime-timeout.txt").is_file() or runtime_status == 124
    last_completed = operations[-1] if operations else None
    next_expected = expected[len(operations)] if len(operations) < len(expected) else None

    classification = "pass"
    if timed_out:
        classification = "runtime-timeout"
    elif runtime_status in {126, 127}:
        classification = "runtime-start-failure"
    elif runtime_status not in {0, None} and not initiated:
        classification = "task-initiation-failure"
    elif irq_delta is None or irq_delta <= 0:
        classification = "initiated-without-irq" if initiated else "task-initiation-failure"
    elif not operations:
        classification = "irq-without-completion"
    elif operations != expected:
        classification = "partial-operation-sequence"
    elif not actual_output:
        classification = "missing-output"
    elif actual_output != EXPECTED_LENET_OUTPUT:
        classification = "output-mismatch"
    elif runtime_status != 0:
        classification = "runtime-failure"

    return {
        "index": index,
        "status": "pass" if classification == "pass" else "fail",
        "classification": classification,
        "target_classification": result_env.get("classification"),
        "runtime_status": runtime_status,
        "timed_out": timed_out,
        "task_initiated": initiated,
        "irq_delta": irq_delta,
        "operations": operations,
        "expected_operations": expected,
        "operation_sequence_match": operations == expected,
        "last_completed": last_completed,
        "next_expected": next_expected,
        "actual_output": actual_output or None,
        "expected_output": EXPECTED_LENET_OUTPUT,
        "output_sha256": sha256_file(output_path) if output_path.is_file() else None,
    }


def _analyze_lenet(root: Path, result: dict[str, str]) -> dict[str, Any]:
    requested = int(result.get("repeat_requested", "1"))
    repeat_paths: dict[int, Path] = {}
    for path in root.glob("repeat-*"):
        match = re.fullmatch(r"repeat-(\d+)", path.name)
        if path.is_dir() and match:
            repeat_paths[int(match.group(1))] = path

    repeats = []
    for index in range(1, requested + 1):
        path = repeat_paths.get(index)
        if path is None:
            repeats.append(
                {
                    "index": index,
                    "status": "fail",
                    "classification": "missing-repeat-evidence",
                    "operations": [],
                    "expected_operations": EXPECTED_LENET_OPERATIONS,
                    "actual_output": None,
                    "expected_output": EXPECTED_LENET_OUTPUT,
                }
            )
        else:
            repeats.append(_classify_lenet_repeat(path, index))

    pass_count = sum(item["status"] == "pass" for item in repeats)
    first_failure = next((item for item in repeats if item["status"] != "pass"), None)
    hashes = sorted(
        {
            item["output_sha256"]
            for item in repeats
            if item.get("output_sha256") is not None
        }
    )
    classification = (
        first_failure["classification"]
        if first_failure
        else "exact-pass"
        if requested == 1
        else "stability-pass"
    )
    return {
        "kind": "lenet",
        "status": "pass" if first_failure is None else "fail",
        "classification": classification,
        "repeat_requested": requested,
        "pass_count": pass_count,
        "first_failure": first_failure,
        "repeat_results": repeats,
        "distinct_output_hashes": hashes,
    }


def _analyze_sdp(root: Path, result: dict[str, str]) -> dict[str, Any]:
    client_status = _read_int(root / "runtime-client.exit-status")
    server_status = _read_int(root / "runtime-server.exit-status")
    dmesg = _read_text(root / "sdp-dmesg-delta.log") or _read_text(root / "dmesg-delta.log")
    client = _read_text(root / "runtime-client.stdout.log")
    output = root / "runtime-output" / "o_000000.dimg"
    irq_delta = _read_int(root / "sdp-irq-delta.txt")
    compare_status = _read_text(root / "sdp-compare-status.txt").strip() or None
    payload_nonzero = _read_int(root / "sdp-payload-nonzero-bytes.txt")
    initiated = "Exit: dla_initiate_processors status=0" in dmesg
    completed = "Completed SDP operation" in dmesg
    protocol_passed = "[OK] Test PASSED!" in client
    timed_out = (root / "runtime-timeout.txt").is_file() or client_status == 124

    if timed_out:
        classification = "runtime-timeout"
    elif client_status in {126, 127}:
        classification = "runtime-start-failure"
    elif client_status != 0 and not initiated:
        classification = "task-initiation-failure"
    elif irq_delta is None or irq_delta <= 0:
        classification = "initiated-without-irq" if initiated else "task-initiation-failure"
    elif not completed:
        classification = "irq-without-completion"
    elif not output.is_file():
        classification = "missing-output"
    elif compare_status == "exact":
        classification = "exact-pass"
    elif compare_status == "mismatch" and payload_nonzero == 0 and protocol_passed:
        classification = "diagnostic-pass-oracle-inconclusive"
    else:
        classification = "unexpected-output-mismatch"

    execution_pass = classification in {"exact-pass", "diagnostic-pass-oracle-inconclusive"}
    correctness = (
        "pass"
        if classification == "exact-pass"
        else "inconclusive"
        if classification == "diagnostic-pass-oracle-inconclusive"
        else "fail"
    )
    return {
        "kind": "sdp",
        "status": "pass" if execution_pass else "fail",
        "classification": classification,
        "correctness_status": correctness,
        "client_status": client_status,
        "server_status": server_status,
        "timed_out": timed_out,
        "task_initiated": initiated,
        "irq_delta": irq_delta,
        "sdp_completed": completed,
        "protocol_passed": protocol_passed,
        "compare_status": compare_status,
        "payload_nonzero_bytes": payload_nonzero,
        "output_sha256": sha256_file(output) if output.is_file() else None,
        "output_size_bytes": output.stat().st_size if output.is_file() else None,
    }


def analyze_board_workload(
    root: Path,
    result: dict[str, str],
    bad_patterns: list[str] | None = None,
) -> dict[str, Any] | None:
    mode = result.get("mode")
    if mode == "runtime-sdp":
        analysis = _analyze_sdp(root, result)
    elif mode == "lenet":
        analysis = _analyze_lenet(root, result)
    else:
        return None

    irq = _irq_evidence(root, "interrupts-before.txt", "interrupts-after.txt")
    analysis["irq_evidence"] = irq
    analysis["target_status"] = int(result.get("status", "1"))
    analysis["target_classification"] = result.get("classification")
    analysis["bad_kernel_patterns"] = bad_patterns or []
    if analysis["target_status"] != 0 or analysis["bad_kernel_patterns"]:
        analysis["status"] = "fail"
        if analysis["bad_kernel_patterns"]:
            analysis["classification"] = "kernel-log-failure"
    return analysis


def _safe_extract(archive_path: Path, destination: Path) -> list[str]:
    extracted: list[str] = []
    with tarfile.open(archive_path, "r:*") as archive:
        for member in archive.getmembers():
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise ValueError(f"unsafe board artifact path: {member.name}")
            if member.issym() or member.islnk():
                raise ValueError(f"links are not allowed in board artifacts: {member.name}")
            target = destination.joinpath(*name.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise ValueError(f"unsupported board artifact member: {member.name}")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"could not read board artifact member: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as output:
                shutil.copyfileobj(source, output)
            extracted.append(member.name)
    return sorted(extracted)


def import_board_artifact(
    archive_path: Path,
    out_dir: Path,
    serial_log: Path | None = None,
) -> dict[str, Any]:
    if not archive_path.is_file():
        raise FileNotFoundError(f"board artifact archive does not exist: {archive_path}")
    extract_dir = out_dir / "board"
    extract_dir.mkdir(parents=True, exist_ok=True)
    members = _safe_extract(archive_path, extract_dir)

    result_files = list(extract_dir.rglob("result.env"))
    if not result_files:
        raise ValueError("board artifact does not contain result.env")
    minimum_depth = min(len(path.relative_to(extract_dir).parts) for path in result_files)
    run_results = [
        path
        for path in result_files
        if len(path.relative_to(extract_dir).parts) == minimum_depth
    ]
    if len(run_results) != 1:
        raise ValueError(
            f"expected one run-level result.env in board artifact, found {len(run_results)}"
        )
    run_result = run_results[0]
    result = _parse_env(run_result)
    mode = result.get("mode", "unknown")
    board_status = int(result.get("status", "1"))

    bad_files = list(extract_dir.rglob("bad-kernel-patterns.txt"))
    bad_patterns = []
    if len(bad_files) == 1:
        bad_patterns = [
            line
            for line in bad_files[0].read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip()
        ]

    stored_archive = out_dir / "board-artifact.tar.gz"
    if archive_path.resolve() != stored_archive.resolve():
        shutil.copyfile(archive_path, stored_archive)
    if serial_log:
        if not serial_log.is_file():
            raise FileNotFoundError(f"serial log does not exist: {serial_log}")
        shutil.copyfile(serial_log, out_dir / "serial.log")

    status = "pass" if board_status == 0 and not bad_patterns else "fail"
    workload_analysis = analyze_board_workload(run_result.parent, result, bad_patterns)
    if workload_analysis is not None:
        status = workload_analysis["status"]
        write_json(out_dir / "workload-analysis.json", workload_analysis)
    manifest = {
        "schema_version": 1,
        "lane": f"petalinux-board-{mode}",
        "mode": mode,
        "status": status,
        "board_status": board_status,
        "timestamp_utc": result.get("timestamp_utc"),
        "archive": {
            "path": str(stored_archive),
            "sha256": sha256_file(stored_archive),
        },
        "serial_log": str(out_dir / "serial.log") if serial_log else None,
        "members": members,
        "bad_kernel_patterns": bad_patterns,
        "classification": workload_analysis.get("classification") if workload_analysis else None,
        "workload_analysis": "workload-analysis.json" if workload_analysis else None,
        "workload": workload_analysis,
    }
    write_json(out_dir / "manifest.json", manifest)
    return manifest


def run_board_artifact_import(
    archive_path: Path,
    out_dir: Path,
    serial_log: Path | None,
) -> int:
    try:
        manifest = import_board_artifact(archive_path, out_dir, serial_log)
    except Exception as exc:
        write_json(
            out_dir / "manifest.json",
            {
                "schema_version": 1,
                "lane": "petalinux-board-import",
                "status": "fail",
                "reason": str(exc),
            },
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Board artifact import: {manifest['status']}")
    print(f"Manifest: {out_dir / 'manifest.json'}")
    return 0 if manifest["status"] == "pass" else 1
