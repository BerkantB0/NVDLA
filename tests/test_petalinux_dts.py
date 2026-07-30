from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nvdla_test_framework.petalinux import generate_nvdla_dtsi


ROOT = Path(__file__).resolve().parents[1]


class PetaLinuxDtsTests(unittest.TestCase):
    def test_zcu102_power_fragment_uses_board_monitor_topology(self) -> None:
        text = (
            ROOT / "recipes/petalinux/device-tree/files/zcu102-power.dtsi"
        ).read_text(encoding="utf-8")

        self.assertIn('compatible = "nxp,pca9544";', text)
        self.assertIn("reg = <0x75>;", text)
        self.assertEqual(text.count('compatible = "ti,ina226";'), 18)
        self.assertIn('label = "VCCINT";', text)
        self.assertIn('label = "VCCBRAM";', text)
        self.assertIn('label = "VCCAUX";', text)
        self.assertIn('label = "VCCPSINTFP";', text)
        self.assertIn("shunt-resistor = <2000>;", text)
        self.assertEqual(text.count("shunt-resistor = <5000>;"), 17)

        config = (
            ROOT
            / "recipes/petalinux/kernel/linux-xlnx/files/nvdla-power-monitor.cfg"
        ).read_text(encoding="utf-8")
        self.assertIn("CONFIG_HWMON=y", config)
        self.assertIn("CONFIG_I2C_MUX=y", config)
        self.assertIn("CONFIG_I2C_MUX_PCA954x=y", config)
        self.assertIn("CONFIG_SENSORS_INA2XX=y", config)

    def test_zcu102_ethernet_fragment_uses_board_phy_settings(self) -> None:
        text = (
            ROOT / "recipes/petalinux/device-tree/files/zcu102-ethernet.dtsi"
        ).read_text(encoding="utf-8")

        self.assertIn("local-mac-address = [02 00 00 50 10 02];", text)
        self.assertIn("phy-handle = <&nvdla_zcu102_phy>;", text)
        self.assertIn('phy-mode = "rgmii-id";', text)
        self.assertIn('compatible = "ethernet-phy-id2000.a231";', text)
        self.assertIn("reg = <0xc>;", text)
        self.assertIn("ti,rx-internal-delay = <0x8>;", text)
        self.assertIn("ti,tx-internal-delay = <0xa>;", text)
        self.assertIn("ti,dp83867-rxctrl-strap-quirk;", text)

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

            self.assertIn("&xilNvDlaWrapper_0 {", text)
            self.assertIn('compatible = "nvidia,nv_small";', text)
            self.assertNotIn("nvdla@a0000000", text)
            self.assertNotIn("reg =", text)
            self.assertNotIn("interrupts =", text)
            self.assertNotIn("dma-coherent", text)
            self.assertEqual(result["node"]["generated_node_label"], "xilNvDlaWrapper_0")
            self.assertIn("clocks", result["node"]["preserved_properties"])
            self.assertEqual(
                result["node"]["reg"],
                ["0x0", "0xa0000000", "0x0", "0x00010000"],
            )
            self.assertEqual(result["node"]["interrupt_source_port"], "pl_ps_irq0")
            self.assertEqual(result["node"]["interrupt_parent"], "gic")
            self.assertEqual(result["node"]["interrupts"], [0, 89, 4])
            self.assertFalse(result["node"]["dma_coherent"])


if __name__ == "__main__":
    unittest.main()
