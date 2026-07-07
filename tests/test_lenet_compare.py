from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nvdla_test_framework.lenet import compare_lenet_control
from nvdla_test_framework.common import read_json


class LenetCompareTests(unittest.TestCase):
    def test_classifies_clean_output_mismatch_after_all_layers(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            stock = root / "stock"
            modern = root / "modern"
            (modern / "runtime-output").mkdir(parents=True)
            stock.mkdir()

            (stock / "output-nvfull-visible.txt").write_text("0 2 0 0 0 0 0 124 0 0\n", encoding="utf-8")
            (stock / "dmesg-nvfull-visible.log").write_text(
                "Completed SDP operation index 9 ROI 0\n"
                "10 HWLs done, totally 10 layers\n",
                encoding="utf-8",
            )
            (modern / "manifest.json").write_text(
                """{
  "status": "fail",
  "expected_output": "0 2 0 0 0 0 0 124 0 0",
  "actual_output": "12 12 12 12 12 12 12 12 12 12"
}
""",
                encoding="utf-8",
            )
            (modern / "serial.log").write_text(
                "nvdla-trace submit-before index=1 fd=4 offset=0x0 size=4096 sample=4096 hash=0x1234abcd first=01020304\n"
                "nvdla-trace submit-after index=1 fd=4 offset=0x0 size=4096 sample=4096 hash=0x5678abcd first=05060708\n"
                "Completed SDP operation index 9 ROI 0\n"
                "10 HWLs done, totally 10 layers\n",
                encoding="utf-8",
            )
            (modern / "dmesg.log").write_text("", encoding="utf-8")
            (modern / "runtime-output" / "output.txt").write_text(
                "12 12 12 12 12 12 12 12 12 12\n",
                encoding="utf-8",
            )

            out = root / "compare.json"
            rc = compare_lenet_control(stock, modern, out)
            result = read_json(out)

            self.assertEqual(rc, 1)
            self.assertEqual(result["classification"], "runtime_clean_output_mismatch")
            self.assertFalse(result["comparisons"]["output_match"])
            self.assertEqual(result["modern"]["trace"]["line_count"], 2)
            self.assertEqual(result["modern"]["trace"]["tags"]["submit-after"], 1)

    def test_passes_matching_output_without_bad_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            stock = root / "stock"
            modern = root / "modern"
            (modern / "runtime-output").mkdir(parents=True)
            stock.mkdir()

            output = "0 2 0 0 0 0 0 124 0 0\n"
            (stock / "output-nvfull-visible.txt").write_text(output, encoding="utf-8")
            (stock / "dmesg-nvfull-visible.log").write_text("", encoding="utf-8")
            (modern / "manifest.json").write_text(
                '{"status": "pass", "actual_output": "0 2 0 0 0 0 0 124 0 0"}\n',
                encoding="utf-8",
            )
            (modern / "serial.log").write_text("", encoding="utf-8")
            (modern / "dmesg.log").write_text("", encoding="utf-8")
            (modern / "runtime-output" / "output.txt").write_text(output, encoding="utf-8")

            out = root / "compare.json"
            rc = compare_lenet_control(stock, modern, out)
            result = read_json(out)

            self.assertEqual(rc, 0)
            self.assertEqual(result["classification"], "pass")
            self.assertTrue(result["comparisons"]["output_match"])

    def test_reads_bad_pattern_file_and_avoids_serial_dmesg_double_count(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            stock = root / "stock"
            modern = root / "modern"
            (modern / "runtime-output").mkdir(parents=True)
            stock.mkdir()

            (stock / "output-nvfull-visible.txt").write_text("0 2 0 0 0 0 0 124 0 0\n", encoding="utf-8")
            (stock / "dmesg-nvfull-visible.log").write_text(
                "Completed SDP operation index 9 ROI 0\n"
                "10 HWLs done, totally 10 layers\n",
                encoding="utf-8",
            )
            (modern / "manifest.json").write_text(
                '{"status": "fail", "actual_output": "12 12 12 12 12 12 12 12 12 12"}\n',
                encoding="utf-8",
            )
            layer_log = (
                "Completed SDP operation index 9 ROI 0\n"
                "10 HWLs done, totally 10 layers\n"
            )
            (modern / "serial.log").write_text(layer_log + layer_log, encoding="utf-8")
            (modern / "dmesg.log").write_text(layer_log, encoding="utf-8")
            (modern / "bad-patterns.log").write_text(
                "rcu: INFO: rcu_sched detected stalls on CPUs/tasks:\n",
                encoding="utf-8",
            )
            (modern / "runtime-output" / "output.txt").write_text(
                "12 12 12 12 12 12 12 12 12 12\n",
                encoding="utf-8",
            )

            out = root / "compare.json"
            rc = compare_lenet_control(stock, modern, out)
            result = read_json(out)

            self.assertEqual(rc, 1)
            self.assertEqual(result["classification"], "kernel_log_bad_pattern")
            self.assertEqual(result["comparisons"]["modern_layer_count"], 1)
            self.assertEqual(
                result["modern"]["bad_patterns"],
                ["rcu: INFO: rcu_sched detected stalls on CPUs/tasks:"],
            )


if __name__ == "__main__":
    unittest.main()
