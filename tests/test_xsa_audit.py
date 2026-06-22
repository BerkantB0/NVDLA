from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from nvdla_test_framework.xsa_audit import audit_xsa, validate_audit


ROOT = Path(__file__).resolve().parents[1]


class XsaAuditTests(unittest.TestCase):
    def test_audits_checked_in_xsa_against_lock(self) -> None:
        lock = json.loads((ROOT / "repro.lock.json").read_text(encoding="utf-8"))
        audit = audit_xsa(ROOT / "NVDLA_FPGA_wrapper.xsa")
        errors = validate_audit(audit, lock)
        self.assertEqual(errors, [])
        self.assertEqual(audit["wrapper"]["range_size"], 0x10000)

    def test_synthetic_xsa_reports_dbb_and_irq(self) -> None:
        hwh = """<?xml version="1.0"?>
<SYSTEM>
  <MODULE INSTANCE="xilNvDlaWrapper_0">
    <PARAMETER NAME="C_BASEADDR" VALUE="0xA0000000"/>
    <PARAMETER NAME="C_HIGHADDR" VALUE="0xA000FFFF"/>
    <PORT NAME="dla_intr" DIR="O" SENSITIVITY="LEVEL_HIGH">
      <CONNECTION INSTANCE="zynq_ultra_ps_e_0" PORT="pl_ps_irq0"/>
    </PORT>
    <BUSINTERFACE NAME="m_axi" TYPE="MASTER" DATAWIDTH="64"/>
  </MODULE>
  <MEMRANGE INSTANCE="zynq_ultra_ps_e_0" MASTERBUSINTERFACE="m_axi" SLAVEBUSINTERFACE="S_AXI_HP0_FPD"/>
  <PARAMETER NAME="PSU__AFI0_COHERENCY" VALUE="0"/>
</SYSTEM>
"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "test.xsa"
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("NVDLA_FPGA.hwh", hwh)
            audit = audit_xsa(path)
        self.assertEqual(audit["wrapper"]["base"], "0xA0000000")
        self.assertEqual(audit["wrapper"]["interrupt"]["connections"][0]["port"], "pl_ps_irq0")
        self.assertIn("S_AXI_HP0_FPD", audit["memory"]["dbb_slave_interfaces"])


if __name__ == "__main__":
    unittest.main()

