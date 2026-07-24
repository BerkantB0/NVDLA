from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PetaLinuxDiagnosticTests(unittest.TestCase):
    def test_diagnostic_module_is_not_in_production_image_append(self) -> None:
        image_append = (
            ROOT
            / "recipes"
            / "petalinux"
            / "images"
            / "nvdla-stack"
            / "petalinux-image-minimal.bbappend"
        ).read_text(encoding="utf-8")
        self.assertNotIn("opendla-diagnostic", image_append)

    def test_diagnostic_recipe_enables_trace_and_deploys_standalone_module(self) -> None:
        recipe = (
            ROOT
            / "recipes"
            / "petalinux"
            / "modules"
            / "opendla-diagnostic"
            / "opendla-diagnostic.bb"
        ).read_text(encoding="utf-8")
        self.assertIn('EXTRA_OEMAKE += "NVDLA_HW_CONFIG=small"', recipe)
        self.assertIn('EXTRA_OEMAKE += "NVDLA_KMD_TRACE=1"', recipe)
        self.assertIn("${DEPLOYDIR}/opendla-diagnostic.ko", recipe)

    def test_debug_queue_brackets_csb_read(self) -> None:
        patch = (
            ROOT
            / "patches"
            / "debug"
            / "nvdla-sw"
            / "0002-debug-trace-NVDLA-CSB-register-reads.patch"
        ).read_text(encoding="utf-8")
        self.assertIn("nvdla-trace csb-read begin", patch)
        self.assertIn("nvdla-trace csb-read end", patch)
