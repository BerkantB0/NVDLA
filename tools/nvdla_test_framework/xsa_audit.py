from __future__ import annotations

import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .common import read_json, sha256_file, write_json


class AuditError(RuntimeError):
    pass


def _attr_int(value: str | None) -> int | None:
    if value is None:
        return None
    return int(value, 0)


def _iter_named(element: ET.Element, tag: str):
    for child in element.iter():
        if child.tag.split("}")[-1] == tag:
            yield child


def _find_module(root: ET.Element, instance: str) -> ET.Element:
    for module in _iter_named(root, "MODULE"):
        if module.attrib.get("INSTANCE") == instance:
            return module
    raise AuditError(f"module instance {instance!r} not found")


def _parameters(element: ET.Element) -> dict[str, str]:
    out: dict[str, str] = {}
    for param in _iter_named(element, "PARAMETER"):
        name = param.attrib.get("NAME")
        value = param.attrib.get("VALUE")
        if name and value is not None:
            out[name] = value
    return out


def audit_xsa(xsa_path: Path) -> dict[str, Any]:
    if not xsa_path.exists():
        raise AuditError(f"XSA not found: {xsa_path}")

    with zipfile.ZipFile(xsa_path) as zf:
        names = sorted(zf.namelist())
        if "NVDLA_FPGA.hwh" not in names:
            raise AuditError("NVDLA_FPGA.hwh not found in XSA")
        hwh_text = zf.read("NVDLA_FPGA.hwh").decode("utf-8", errors="replace")

    root = ET.fromstring(hwh_text)
    wrapper = _find_module(root, "xilNvDlaWrapper_0")
    wrapper_params = _parameters(wrapper)

    interrupt: dict[str, Any] = {}
    for port in _iter_named(wrapper, "PORT"):
        if port.attrib.get("NAME") == "dla_intr":
            interrupt = {
                "name": "dla_intr",
                "direction": port.attrib.get("DIR"),
                "sensitivity": port.attrib.get("SENSITIVITY"),
                "signame": port.attrib.get("SIGNAME"),
                "connections": [
                    {
                        "instance": c.attrib.get("INSTANCE"),
                        "port": c.attrib.get("PORT"),
                    }
                    for c in _iter_named(port, "CONNECTION")
                ],
            }
            break

    bus_interfaces = []
    for bus in _iter_named(wrapper, "BUSINTERFACE"):
        bus_interfaces.append(
            {
                "name": bus.attrib.get("NAME"),
                "type": bus.attrib.get("TYPE"),
                "busname": bus.attrib.get("BUSNAME"),
                "datawidth": _attr_int(bus.attrib.get("DATAWIDTH")),
            }
        )

    memranges = []
    for memrange in _iter_named(root, "MEMRANGE"):
        item = dict(memrange.attrib)
        if item.get("MASTERBUSINTERFACE") == "m_axi" or item.get("INSTANCE") == "xilNvDlaWrapper_0":
            memranges.append(item)

    all_params = _parameters(root)
    coherency = {
        name: value
        for name, value in all_params.items()
        if "COHERENCY" in name.upper()
    }

    return {
        "xsa": {
            "path": str(xsa_path),
            "sha256": sha256_file(xsa_path),
            "members": names,
        },
        "wrapper": {
            "instance": "xilNvDlaWrapper_0",
            "base": wrapper_params.get("C_BASEADDR"),
            "high": wrapper_params.get("C_HIGHADDR"),
            "range_size": (
                _attr_int(wrapper_params.get("C_HIGHADDR"))
                - _attr_int(wrapper_params.get("C_BASEADDR"))
                + 1
                if wrapper_params.get("C_BASEADDR") and wrapper_params.get("C_HIGHADDR")
                else None
            ),
            "interrupt": interrupt,
            "bus_interfaces": bus_interfaces,
        },
        "memory": {
            "ranges": memranges,
            "dbb_slave_interfaces": sorted(
                {
                    m.get("SLAVEBUSINTERFACE")
                    for m in memranges
                    if m.get("MASTERBUSINTERFACE") == "m_axi" and m.get("SLAVEBUSINTERFACE")
                }
            ),
        },
        "coherency": {
            "parameters": coherency,
            "nonzero": {
                name: value
                for name, value in coherency.items()
                if value not in {"0", "0x0", "false", "False"}
            },
        },
    }


def validate_audit(audit: dict[str, Any], lock: dict[str, Any]) -> list[str]:
    expected = lock["hardware"]["xsa"]["expected"]
    errors: list[str] = []

    def expect(label: str, actual: Any, wanted: Any) -> None:
        if actual != wanted:
            errors.append(f"{label}: expected {wanted!r}, got {actual!r}")

    expect("XSA SHA256", audit["xsa"]["sha256"], lock["hardware"]["xsa"]["sha256"])
    expect("wrapper instance", audit["wrapper"]["instance"], expected["wrapper_instance"])
    expect("CSB base", audit["wrapper"]["base"], expected["csb_base"])
    expect("CSB high", audit["wrapper"]["high"], expected["csb_high"])

    intr = audit["wrapper"]["interrupt"]
    expect("interrupt name", intr.get("name"), expected["interrupt_output"])
    expect("interrupt sensitivity", intr.get("sensitivity"), expected["interrupt_sensitivity"])
    connected_ports = {c.get("port") for c in intr.get("connections", [])}
    if expected["interrupt_ps_port"] not in connected_ports:
        errors.append(
            f"interrupt connection: expected port {expected['interrupt_ps_port']!r}, got {sorted(connected_ports)!r}"
        )

    widths = {
        b.get("datawidth")
        for b in audit["wrapper"]["bus_interfaces"]
        if b.get("name") == "m_axi" and b.get("type") == "MASTER"
    }
    if expected["dbb_axi_data_width"] not in widths:
        errors.append(
            f"DBB AXI data width: expected {expected['dbb_axi_data_width']}, got {sorted(widths)!r}"
        )

    dbb_slaves = set(audit["memory"]["dbb_slave_interfaces"])
    if expected["dbb_memory_slave"] not in dbb_slaves:
        errors.append(
            f"DBB memory slave: expected {expected['dbb_memory_slave']!r}, got {sorted(dbb_slaves)!r}"
        )

    coherent = bool(audit["coherency"]["nonzero"])
    if coherent != expected["coherent_dma"]:
        errors.append(
            f"coherent DMA expectation: expected {expected['coherent_dma']}, got nonzero coherency params {audit['coherency']['nonzero']!r}"
        )
    return errors


def run_xsa_audit(xsa_path: Path, lock_path: Path, out_path: Path | None) -> int:
    try:
        lock = read_json(lock_path)
        audit = audit_xsa(xsa_path)
        errors = validate_audit(audit, lock)
        result = {"status": "fail" if errors else "pass", "errors": errors, "audit": audit}
        if out_path:
            write_json(out_path, result)
        if errors:
            for error in errors:
                print(f"ERROR: {error}", file=sys.stderr)
            return 1
        print("XSA audit passed")
        print(f"  CSB: {audit['wrapper']['base']}..{audit['wrapper']['high']}")
        print(f"  IRQ: dla_intr -> {lock['hardware']['xsa']['expected']['interrupt_ps_port']}")
        print(f"  DBB: {', '.join(audit['memory']['dbb_slave_interfaces'])}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

