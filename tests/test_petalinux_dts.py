from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nvdla_test_framework.petalinux import generate_nvdla_dtsi


ROOT = Path(__file__).resolve().parents[1]


class PetaLinuxDtsTests(unittest.TestCase):
    def test_generates_nv_small_node_from_checked_in_xsa(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "nvdla-user.dtsi"
            audit = Path(td) / "audit.json"
            result = generate_nvdla_dtsi(
                ROOT / "repro.lock.json",
                ROOT / "NVDLA_FPGA_wrapper.xsa",
                out,
                audit,
            )
            text = out.read_text(encoding="utf-8")
            self.assertTrue(audit.is_file())

            self.assertIn('compatible = "nvidia,nv_small";', text)
            self.assertIn("reg = <0x0 0xa0000000 0x0 0x00010000>;", text)
            self.assertIn("interrupts = <0 89 4>;", text)
            self.assertNotIn("dma-coherent", text)
            self.assertEqual(result["node"]["interrupt_source_port"], "pl_ps_irq0")
            self.assertEqual(result["node"]["interrupts"], [0, 89, 4])
            self.assertFalse(result["node"]["dma_coherent"])


if __name__ == "__main__":
    unittest.main()
