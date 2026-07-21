from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .common import write_json


TRACE_SCHEMA_VERSION = 1
DEFAULT_CSB_BASE = 0x10200000
DEFAULT_EXTMEM_BASE = 0xC0000000
DEFAULT_EXTMEM_HIGH = 0xFFFFFFFF

_TRANSACTION_RE = re.compile(
    r"nvdla\.(?P<interface>csb|dbb)_adaptor:\s+GP:\s+"
    r"iswrite=(?P<iswrite>[01])\s+"
    r"addr=0x(?P<address>[0-9a-fA-F]+)\s+"
    r"len=(?P<length>\d+)\s+"
    r"data=0x(?P<data>.*?)\s+"
    r"resp=TLM_(?P<response>[A-Z_]+)_RESPONSE"
)
_REGISTER_RE = re.compile(
    r"^#define\s+(?P<name>[A-Z][A-Z0-9_]+)\s+"
    r"_MK_ADDR_CONST\((?P<address>0x[0-9a-fA-F]+)\)\s*$"
)


def parse_register_map(header: Path) -> dict[int, str]:
    registers: dict[int, str] = {}
    for line in header.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _REGISTER_RE.match(line)
        if not match:
            continue
        address = int(match.group("address"), 16)
        registers.setdefault(address, match.group("name"))
    if not registers:
        raise ValueError(f"no NVDLA register definitions found in {header}")
    return registers


def _format_data(value: str) -> str:
    words = re.findall(r"[0-9a-fA-F]+", value)
    if not words:
        raise ValueError("transaction has no hexadecimal data")
    return "0x" + "".join(words).lower()


def _response_name(value: str) -> str:
    return value.lower().removesuffix("_response")


def parse_vp_transactions(
    lines: Iterable[str],
    registers: dict[int, str] | None = None,
    csb_base: int = DEFAULT_CSB_BASE,
    source: str = "vp",
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    registers = registers or {}
    for line_number, line in enumerate(lines, start=1):
        if "nvdla.csb_adaptor: GP:" not in line and "nvdla.dbb_adaptor: GP:" not in line:
            continue
        match = _TRANSACTION_RE.search(line)
        if not match:
            raise ValueError(f"malformed VP transaction at line {line_number}: {line.strip()}")

        interface = match.group("interface")
        address = int(match.group("address"), 16)
        offset = address - csb_base if interface == "csb" and address >= csb_base else address
        response = _response_name(match.group("response"))
        event: dict[str, Any] = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "sequence": len(events),
            "source": source,
            "interface": interface,
            "operation": "write" if match.group("iswrite") == "1" else "read",
            "offset": f"0x{offset:08x}",
            "length": int(match.group("length")),
            "data": _format_data(match.group("data")),
            "response": response,
            "register": registers.get(offset) if interface == "csb" else None,
        }
        events.append(event)
    return events


def write_jsonl(path: Path, events: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for event in events:
            output.write(json.dumps(event, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
            if event.get("schema_version") != TRACE_SCHEMA_VERSION:
                raise ValueError(f"unsupported trace schema at {path}:{line_number}")
            events.append(event)
    return events


def split_raw_transactions(lines: Iterable[str]) -> tuple[list[str], list[str]]:
    csb: list[str] = []
    dbb: list[str] = []
    for line in lines:
        if "nvdla.csb_adaptor: GP:" in line:
            csb.append(line.rstrip("\n"))
        elif "nvdla.dbb_adaptor: GP:" in line:
            dbb.append(line.rstrip("\n"))
    return csb, dbb


def summarize_trace(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_interface = Counter(event["interface"] for event in events)
    by_operation = Counter(f"{event['interface']}:{event['operation']}" for event in events)
    responses = Counter(event["response"] for event in events)
    registers = Counter(event.get("register") or event["offset"] for event in events if event["interface"] == "csb")
    return {
        "schema_version": TRACE_SCHEMA_VERSION,
        "event_count": len(events),
        "by_interface": dict(sorted(by_interface.items())),
        "by_operation": dict(sorted(by_operation.items())),
        "responses": dict(sorted(responses.items())),
        "non_ok_responses": sum(count for response, count in responses.items() if response != "ok"),
        "registers": dict(sorted(registers.items())),
    }


def canonicalize_vp_trace(
    input_path: Path,
    register_header: Path,
    csb_out: Path,
    raw_csb_out: Path,
    raw_dbb_out: Path,
    summary_out: Path,
    csb_base: int = DEFAULT_CSB_BASE,
) -> dict[str, Any]:
    registers = parse_register_map(register_header)
    by_interface: Counter[str] = Counter()
    by_operation: Counter[str] = Counter()
    responses: Counter[str] = Counter()
    register_counts: Counter[str] = Counter()
    event_count = 0
    csb_event_count = 0

    csb_out.parent.mkdir(parents=True, exist_ok=True)
    raw_csb_out.parent.mkdir(parents=True, exist_ok=True)
    raw_dbb_out.parent.mkdir(parents=True, exist_ok=True)
    with (
        input_path.open("r", encoding="utf-8", errors="replace") as source,
        csb_out.open("w", encoding="utf-8") as canonical_csb,
        raw_csb_out.open("w", encoding="utf-8") as raw_csb,
        raw_dbb_out.open("w", encoding="utf-8") as raw_dbb,
    ):
        for line_number, line in enumerate(source, start=1):
            is_csb = "nvdla.csb_adaptor: GP:" in line
            is_dbb = "nvdla.dbb_adaptor: GP:" in line
            if not is_csb and not is_dbb:
                continue
            try:
                event = parse_vp_transactions([line], registers, csb_base=csb_base)[0]
            except ValueError as exc:
                raise ValueError(f"{exc} (source line {line_number})") from exc

            event_count += 1
            by_interface[event["interface"]] += 1
            by_operation[f"{event['interface']}:{event['operation']}"] += 1
            responses[event["response"]] += 1
            if is_csb:
                event["sequence"] = csb_event_count
                csb_event_count += 1
                register_counts[event.get("register") or event["offset"]] += 1
                canonical_csb.write(json.dumps(event, sort_keys=True) + "\n")
                raw_csb.write(line)
            else:
                raw_dbb.write(line)

    summary = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "event_count": event_count,
        "by_interface": dict(sorted(by_interface.items())),
        "by_operation": dict(sorted(by_operation.items())),
        "responses": dict(sorted(responses.items())),
        "non_ok_responses": sum(count for response, count in responses.items() if response != "ok"),
        "registers": dict(sorted(register_counts.items())),
        "csb_event_count": csb_event_count,
        "dbb_event_count": event_count - csb_event_count,
    }
    write_json(summary_out, summary)
    return summary


def run_trace_parse(
    input_path: Path,
    register_header: Path,
    csb_out: Path,
    raw_csb_out: Path,
    raw_dbb_out: Path,
    summary_out: Path,
    csb_base: int,
) -> int:
    try:
        summary = canonicalize_vp_trace(
            input_path,
            register_header,
            csb_out,
            raw_csb_out,
            raw_dbb_out,
            summary_out,
            csb_base,
        )
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"Canonical VP trace: {summary['csb_event_count']} CSB, {summary['dbb_event_count']} DBB events")
    return 0 if summary["non_ok_responses"] == 0 and summary["csb_event_count"] > 0 else 1


def is_dma_address_register(register: str | None) -> bool:
    if not register or "ADDR" not in register:
        return False
    return any(token in register for token in ("BASE_ADDR", "DAIN_ADDR", "WEIGHT_ADDR", "DATAOUT_ADDR"))


def is_state_read(event: dict[str, Any]) -> bool:
    if event.get("operation") != "read":
        return False
    register = event.get("register") or ""
    return any(token in register for token in ("_S_STATUS", "_S_POINTER", "INTR_STATUS", "INTR_MASK"))


def _validate_dma_value(event: dict[str, Any], extmem_base: int, extmem_high: int) -> str | None:
    register = event.get("register") or ""
    value = int(event["data"], 16)
    if "HIGH" in register:
        if value != 0:
            return f"{register} has unsupported non-zero high address 0x{value:08x}"
        return None
    if value == 0 and "DATAOUT_ADDR" in register:
        return None
    if not extmem_base <= value <= extmem_high:
        return f"{register} address 0x{value:08x} is outside extmem"
    if value % 4:
        return f"{register} address 0x{value:08x} is not 4-byte aligned"
    return None


def normalize_csb_events(
    events: list[dict[str, Any]],
    extmem_base: int = DEFAULT_EXTMEM_BASE,
    extmem_high: int = DEFAULT_EXTMEM_HIGH,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    last_state: dict[str, str] = {}
    masked_addresses = 0
    collapsed_reads = 0
    errors: list[str] = []

    for event in events:
        if event.get("interface") != "csb":
            continue
        if event.get("response") != "ok":
            errors.append(f"sequence {event.get('sequence')}: non-OK response {event.get('response')}")

        register = event.get("register")
        if is_state_read(event):
            state_key = register or event["offset"]
            if last_state.get(state_key) == event["data"]:
                collapsed_reads += 1
                continue
            last_state[state_key] = event["data"]

        item = {
            "operation": event["operation"],
            "offset": event["offset"],
            "length": event["length"],
            "data": event["data"],
            "response": event["response"],
            "register": register,
        }
        if event["operation"] == "write" and is_dma_address_register(register):
            error = _validate_dma_value(event, extmem_base, extmem_high)
            if error:
                errors.append(f"sequence {event.get('sequence')}: {error}")
            item["data"] = "<dma-address>"
            masked_addresses += 1
        normalized.append(item)

    return normalized, {
        "input_count": len(events),
        "normalized_count": len(normalized),
        "masked_address_count": masked_addresses,
        "collapsed_read_count": collapsed_reads,
        "errors": errors,
    }


def _event_signature(event: dict[str, Any]) -> tuple[Any, ...]:
    return (event["operation"], event["offset"], event["length"], event["data"], event["response"])


def _event_context(events: list[dict[str, Any]], index: int, radius: int = 2) -> list[dict[str, Any]]:
    start = max(0, index - radius)
    stop = min(len(events), index + radius + 1)
    return [{"index": current, **events[current]} for current in range(start, stop)]


def _transaction_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    registers: Counter[str] = Counter()
    engines: Counter[str] = Counter()
    operations: Counter[str] = Counter()
    for event in events:
        register = event.get("register") or event["offset"]
        engine = register.split("_", 1)[0]
        registers[register] += 1
        engines[engine] += 1
        operations[event["operation"]] += 1
    return {
        "event_count": len(events),
        "operations": dict(sorted(operations.items())),
        "engines": dict(sorted(engines.items())),
        "registers": dict(sorted(registers.items())),
    }


def _compare_sequence(reference: list[dict[str, Any]], candidate: list[dict[str, Any]]) -> dict[str, Any]:
    reference_signatures = [_event_signature(event) for event in reference]
    candidate_signatures = [_event_signature(event) for event in candidate]
    first_index: int | None = None
    for index, (expected, actual) in enumerate(zip(reference_signatures, candidate_signatures)):
        if expected != actual:
            first_index = index
            break
    if first_index is None and len(reference) != len(candidate):
        first_index = min(len(reference), len(candidate))

    reference_counter = Counter(reference_signatures)
    candidate_counter = Counter(candidate_signatures)
    missing = sum((reference_counter - candidate_counter).values())
    unexpected = sum((candidate_counter - reference_counter).values())
    reordered = 0
    value_mismatched = 0
    for expected, actual in zip(reference, candidate):
        if _event_signature(expected) == _event_signature(actual):
            continue
        same_location = expected["operation"] == actual["operation"] and expected["offset"] == actual["offset"]
        if same_location and expected["data"] != actual["data"]:
            value_mismatched += 1
        elif _event_signature(expected) in candidate_counter and _event_signature(actual) in reference_counter:
            reordered += 1

    first_mismatch = None
    if first_index is not None:
        first_mismatch = {
            "index": first_index,
            "reference_context": _event_context(reference, min(first_index, max(0, len(reference) - 1))) if reference else [],
            "candidate_context": _event_context(candidate, min(first_index, max(0, len(candidate) - 1))) if candidate else [],
        }
    return {
        "match": reference_signatures == candidate_signatures,
        "counts": {
            "reference": len(reference),
            "candidate": len(candidate),
            "missing": missing,
            "unexpected": unexpected,
            "reordered": reordered,
            "value_mismatched": value_mismatched,
        },
        "first_mismatch": first_mismatch,
    }


def _comparison_channel(event: dict[str, Any]) -> str:
    register = event.get("register") or ""
    if register == "GLB_S_INTR_STATUS_0":
        return "interrupt"
    return "programming"


def compare_normalized_traces(
    reference: list[dict[str, Any]], candidate: list[dict[str, Any]]
) -> dict[str, Any]:
    channels: dict[str, Any] = {}
    for channel in ("programming", "interrupt"):
        reference_channel = [event for event in reference if _comparison_channel(event) == channel]
        candidate_channel = [event for event in candidate if _comparison_channel(event) == channel]
        channels[channel] = _compare_sequence(reference_channel, candidate_channel)

    first_mismatch = None
    for channel in ("programming", "interrupt"):
        if channels[channel]["first_mismatch"] is not None:
            first_mismatch = {"channel": channel, **channels[channel]["first_mismatch"]}
            break
    count_fields = ("reference", "candidate", "missing", "unexpected", "reordered", "value_mismatched")
    counts = {
        field: sum(channels[channel]["counts"][field] for channel in channels)
        for field in count_fields
    }
    return {
        "match": all(channel["match"] for channel in channels.values()),
        "counts": counts,
        "first_mismatch": first_mismatch,
        "channels": channels,
        "reference_summary": _transaction_summary(reference),
        "candidate_summary": _transaction_summary(candidate),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_manifest(artifact: Path) -> dict[str, Any]:
    return json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))


def _manifest_input(manifest: dict[str, Any], name: str) -> dict[str, Any]:
    return manifest.get("inputs", {}).get(name, {})


def _layer_completion_count(artifact: Path) -> int:
    serial = artifact / "serial.log"
    if not serial.exists():
        return 0
    values = re.findall(r"(\d+)\s+HWLs done,\s+totally\s+10\s+layers", serial.read_text(errors="replace"))
    return max((int(value) for value in values), default=0)


def _output_path(artifact: Path, manifest: dict[str, Any]) -> Path | None:
    relative = manifest.get("artifacts", {}).get("output")
    if not relative:
        return None
    path = artifact / relative
    return path if path.exists() else None


def _evidence_hashes(manifest: dict[str, Any], reference: bool) -> dict[str, Any]:
    vp = _manifest_input(manifest, "vp_binary") if reference else manifest.get("vp_binary", {})
    cmod = _manifest_input(manifest, "cmod") if reference else manifest.get("vp_cmod", {})
    module_name = "nvdla_module" if reference else "module"
    return {
        "vp_binary": vp.get("sha256"),
        "cmod": cmod.get("sha256"),
        "kernel": _manifest_input(manifest, "kernel").get("sha256"),
        "rootfs": _manifest_input(manifest, "rootfs").get("sha256"),
        "module": _manifest_input(manifest, module_name).get("sha256"),
        "runtime": _manifest_input(manifest, "runtime").get("sha256"),
        "runtime_library": _manifest_input(manifest, "runtime_library").get("sha256"),
        "loadable": _manifest_input(manifest, "loadable").get("sha256"),
        "image": _manifest_input(manifest, "image").get("sha256"),
    }


def compare_trace_artifacts(reference_artifact: Path, candidate_artifact: Path, out: Path) -> dict[str, Any]:
    reference_manifest = _load_manifest(reference_artifact)
    candidate_manifest = _load_manifest(candidate_artifact)
    reference_events = read_jsonl(reference_artifact / "csb-events.jsonl")
    candidate_events = read_jsonl(candidate_artifact / "csb-events.jsonl")
    reference_normalized, reference_policy = normalize_csb_events(reference_events)
    candidate_normalized, candidate_policy = normalize_csb_events(candidate_events)
    trace = compare_normalized_traces(reference_normalized, candidate_normalized)

    reference_vp = _manifest_input(reference_manifest, "vp_binary") or reference_manifest.get("vp_binary", {})
    candidate_vp = candidate_manifest.get("vp_binary", {})
    reference_cmod = _manifest_input(reference_manifest, "cmod") or reference_manifest.get("vp_cmod", {})
    candidate_cmod = candidate_manifest.get("vp_cmod", {})
    configuration_match = {
        "vp_binary": reference_vp.get("sha256") == candidate_vp.get("sha256"),
        "cmod": reference_cmod.get("sha256") == candidate_cmod.get("sha256"),
        "loadable": _manifest_input(reference_manifest, "loadable").get("sha256")
        == _manifest_input(candidate_manifest, "loadable").get("sha256"),
        "image": _manifest_input(reference_manifest, "image").get("sha256")
        == _manifest_input(candidate_manifest, "image").get("sha256"),
    }
    outputs_match = reference_manifest.get("actual_output") == candidate_manifest.get("actual_output")
    reference_output = _output_path(reference_artifact, reference_manifest)
    candidate_output = _output_path(candidate_artifact, candidate_manifest)

    if reference_manifest.get("status") != "pass":
        classification = "reference_invalid"
    elif candidate_manifest.get("status") != "pass":
        classification = "output_mismatch" if not outputs_match else "runtime_failure"
    elif not outputs_match:
        classification = "output_mismatch"
    elif not all(configuration_match.values()) or reference_policy["errors"] or candidate_policy["errors"] or not trace["match"]:
        classification = "trace_mismatch"
    else:
        classification = "pass"

    policy_document = {
        "schema_version": 1,
        "dma_address_values": "masked_after_extmem_range_and_alignment_validation",
        "state_reads": "unique_transitions_per_register",
        "ordinary_writes": "exact_offset_value_order",
        "interrupt_interleaving": "programming_and_interrupt_streams_are_independently_ordered",
        "strict_control": ["operation-enable", "interrupt-mask", "interrupt-status", "interrupt-clear"],
    }
    result = {
        "schema_version": 1,
        "classification": classification,
        "status": "pass" if classification == "pass" else "fail",
        "reference_artifact": str(reference_artifact.resolve()),
        "candidate_artifact": str(candidate_artifact.resolve()),
        "configuration_match": configuration_match,
        "outputs_match": outputs_match,
        "output": {
            "reference": reference_manifest.get("actual_output"),
            "candidate": candidate_manifest.get("actual_output"),
            "reference_sha256": _sha256(reference_output) if reference_output else None,
            "candidate_sha256": _sha256(candidate_output) if candidate_output else None,
            "reference_layer_completion_count": _layer_completion_count(reference_artifact),
            "candidate_layer_completion_count": _layer_completion_count(candidate_artifact),
        },
        "normalization": {"reference": reference_policy, "candidate": candidate_policy},
        "trace": trace,
        "policy": policy_document,
        "policy_sha256": hashlib.sha256(json.dumps(policy_document, sort_keys=True).encode()).hexdigest(),
    }

    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "trace-diff.json", result)
    first = trace["first_mismatch"]
    markdown = [
        "# NVDLA Differential Trace Result",
        "",
        f"- Classification: `{classification}`",
        f"- Reference events: `{trace['counts']['reference']}`",
        f"- Candidate events: `{trace['counts']['candidate']}`",
        f"- Masked DMA writes: `{reference_policy['masked_address_count']}` / `{candidate_policy['masked_address_count']}`",
        f"- Collapsed reads: `{reference_policy['collapsed_read_count']}` / `{candidate_policy['collapsed_read_count']}`",
        f"- First mismatch: `{first['index'] if first else 'none'}`",
    ]
    (out / "trace-diff.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "run_id": out.name,
        "lane": "vp-trace-differential",
        "mode": "trace_lenet_small",
        "status": result["status"],
        "classification": classification,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "reference_artifact": str(reference_artifact.resolve()),
        "candidate_artifact": str(candidate_artifact.resolve()),
        "hashes": {
            "reference_manifest": _sha256(reference_artifact / "manifest.json"),
            "candidate_manifest": _sha256(candidate_artifact / "manifest.json"),
            "reference_raw_trace": _sha256(reference_artifact / "systemc.log"),
            "candidate_raw_trace": _sha256(candidate_artifact / "systemc.log"),
            "reference_canonical_trace": _sha256(reference_artifact / "csb-events.jsonl"),
            "candidate_canonical_trace": _sha256(candidate_artifact / "csb-events.jsonl"),
            "comparison_policy": result["policy_sha256"],
        },
        "evidence": {
            "reference": _evidence_hashes(reference_manifest, reference=True),
            "candidate": _evidence_hashes(candidate_manifest, reference=False),
            "output": result["output"],
            "configuration_match": configuration_match,
        },
        "artifacts": {"diff_json": "trace-diff.json", "diff_markdown": "trace-diff.md"},
    }
    write_json(out / "manifest.json", manifest)
    return result


def run_trace_compare(reference_artifact: Path, candidate_artifact: Path, out: Path) -> int:
    try:
        result = compare_trace_artifacts(reference_artifact, candidate_artifact, out)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"Differential trace: {result['classification']}")
    print(f"Artifacts: {out}")
    return 0 if result["status"] == "pass" else 1
