from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .common import write_json


TRACE_SCHEMA_VERSION = 1
DEFAULT_CSB_BASE = 0x10200000

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
    lines = input_path.read_text(encoding="utf-8", errors="replace").splitlines()
    registers = parse_register_map(register_header)
    events = parse_vp_transactions(lines, registers, csb_base=csb_base)
    csb_events = [event for event in events if event["interface"] == "csb"]
    raw_csb, raw_dbb = split_raw_transactions(lines)

    write_jsonl(csb_out, csb_events)
    raw_csb_out.parent.mkdir(parents=True, exist_ok=True)
    raw_csb_out.write_text("\n".join(raw_csb) + ("\n" if raw_csb else ""), encoding="utf-8")
    raw_dbb_out.parent.mkdir(parents=True, exist_ok=True)
    raw_dbb_out.write_text("\n".join(raw_dbb) + ("\n" if raw_dbb else ""), encoding="utf-8")

    summary = summarize_trace(events)
    summary["csb_event_count"] = len(csb_events)
    summary["dbb_event_count"] = len(events) - len(csb_events)
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
