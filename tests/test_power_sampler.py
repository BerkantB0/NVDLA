from __future__ import annotations

import csv
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools/power/nvdla-power-sampler.c"


@unittest.skipUnless(shutil.which("cc"), "host C compiler is required")
class PowerSamplerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary = self.root / "nvdla-power-sampler"
        build = subprocess.run(
            [
                "cc",
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Werror",
                str(SOURCE),
                "-o",
                str(self.binary),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(build.returncode, 0, build.stderr)

    def _sensor(self, index: int, label: str, power_uw: int) -> None:
        hwmon = self.root / "hwmon" / f"hwmon{index}"
        label_path = hwmon / "device/of_node/label"
        label_path.parent.mkdir(parents=True)
        label_path.write_bytes(label.encode("ascii") + b"\0")
        (hwmon / "power1_input").write_text(f"{power_uw}\n")

    def test_lists_and_samples_ps_and_pl_rails(self) -> None:
        self._sensor(0, "VCCPSINTFP", 1_000_000)
        self._sensor(1, "VCCINT", 500_000)
        self._sensor(2, "MGTRAVCC", 25_000)
        self._sensor(3, "MGTAVCC", 25_000)

        listed = subprocess.run(
            [
                str(self.binary),
                "--list",
                "--hwmon-root",
                str(self.root / "hwmon"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn("PS,VCCPSINTFP", listed.stdout)
        self.assertIn("PL,VCCINT", listed.stdout)
        self.assertIn("PS,MGTRAVCC", listed.stdout)
        self.assertIn("PL,MGTAVCC", listed.stdout)

        output = self.root / "power.csv"
        sampled = subprocess.run(
            [
                str(self.binary),
                "--output",
                str(output),
                "--duration-ms",
                "120",
                "--interval-ms",
                "20",
                "--hwmon-root",
                str(self.root / "hwmon"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(sampled.returncode, 0, sampled.stderr)
        rows = list(
            csv.DictReader(
                line
                for line in output.read_text().splitlines()
                if not line.startswith("#")
            )
        )
        self.assertGreaterEqual(len(rows), 8)
        self.assertEqual({row["domain"] for row in rows}, {"PS", "PL"})
        self.assertEqual(
            {int(row["power_uw"]) for row in rows},
            {1_000_000, 500_000, 25_000},
        )


if __name__ == "__main__":
    unittest.main()
