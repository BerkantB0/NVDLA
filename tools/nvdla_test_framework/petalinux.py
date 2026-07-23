from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from .common import read_json, write_json
from .xsa_audit import audit_xsa, validate_audit


ZYNQMP_PL_PS_IRQ0_DT_HIRQ = 89


def _cell(value: int) -> str:
    if value == 0:
        return "0x0"
    return f"0x{value:08x}"


def _u64_cells(value: int) -> tuple[str, str]:
    return _cell((value >> 32) & 0xFFFFFFFF), _cell(value & 0xFFFFFFFF)


def _interrupt_cells(audit: dict[str, Any]) -> tuple[int, int, int, str]:
    interrupt = audit["wrapper"]["interrupt"]
    ports = [item.get("port") for item in interrupt.get("connections", []) if item.get("port")]
    match = next((re.fullmatch(r"pl_ps_irq(\d+)", port) for port in ports), None)
    if not match:
        raise ValueError(f"cannot derive ZynqMP PL interrupt from ports {ports!r}")
    index = int(match.group(1))
    if index < 0 or index > 15:
        raise ValueError(f"unsupported ZynqMP PL interrupt index {index}")

    sensitivity = interrupt.get("sensitivity")
    flags_by_sensitivity = {
        "EDGE_RISING": 1,
        "LEVEL_HIGH": 4,
    }
    if sensitivity not in flags_by_sensitivity:
        raise ValueError(f"unsupported interrupt sensitivity {sensitivity!r}")
    return 0, ZYNQMP_PL_PS_IRQ0_DT_HIRQ + index, flags_by_sensitivity[sensitivity], f"pl_ps_irq{index}"


def generate_nvdla_dtsi(lock_path: Path, xsa_path: Path, out_path: Path, audit_path: Path | None) -> dict[str, Any]:
    lock = read_json(lock_path)
    audit = audit_xsa(xsa_path)
    errors = validate_audit(audit, lock)
    if errors:
        raise ValueError("; ".join(errors))

    base = int(audit["wrapper"]["base"], 0)
    size = int(audit["wrapper"]["range_size"])
    base_hi, base_lo = _u64_cells(base)
    size_hi, size_lo = _u64_cells(size)
    irq_type, irq_hwirq, irq_flags, irq_port = _interrupt_cells(audit)
    coherent = bool(audit["coherency"]["nonzero"])
    if coherent:
        raise ValueError(f"XSA reports nonzero coherency parameters: {audit['coherency']['nonzero']!r}")

    text = f"""/ {{
    nvdla_0: nvdla@{base:x} {{
        compatible = "nvidia,nv_small";
        reg = <{base_hi} {base_lo} {size_hi} {size_lo}>;
        interrupt-parent = <&gic>;
        interrupts = <{irq_type} {irq_hwirq} {irq_flags}>;
        status = "okay";
        /*
         * No coherent-DMA property: the audited XSA routes DBB through
         * {audit["memory"]["dbb_slave_interfaces"][0]} with coherency disabled.
         */
    }};
}};
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")

    result = {
        "status": "pass",
        "source": {
            "lock": str(lock_path),
            "xsa": str(xsa_path),
            "xsa_sha256": audit["xsa"]["sha256"],
        },
        "node": {
            "compatible": ["nvidia,nv_small"],
            "reg": [base_hi, base_lo, size_hi, size_lo],
            "base": audit["wrapper"]["base"],
            "size": size,
            "interrupt_parent": "gic",
            "interrupts": [irq_type, irq_hwirq, irq_flags],
            "interrupt_source_port": irq_port,
            "dma_coherent": False,
            "dbb_slave_interfaces": audit["memory"]["dbb_slave_interfaces"],
        },
        "output": str(out_path),
    }
    if audit_path:
        write_json(audit_path, result)
    return result


def run_petalinux_dts(lock_path: Path, xsa_path: Path, out_path: Path, audit_path: Path | None) -> int:
    try:
        result = generate_nvdla_dtsi(lock_path, xsa_path, out_path, audit_path)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("PetaLinux DTS fragment generated")
    print(f"  output: {result['output']}")
    print(f"  reg: <{' '.join(result['node']['reg'])}>")
    print(f"  interrupts: <{' '.join(str(cell) for cell in result['node']['interrupts'])}>")
    return 0
