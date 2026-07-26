from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "runtime" / "nvdla-benchmark-launch.c"


@unittest.skipUnless(shutil.which("gcc"), "gcc is required")
class BenchmarkLauncherTests(unittest.TestCase):
    def test_measures_launch_and_enforces_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            binary = root / "launcher"
            build = subprocess.run(
                [
                    "gcc",
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    str(SOURCE),
                    "-o",
                    str(binary),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            elapsed = root / "elapsed.txt"
            result = subprocess.run(
                [str(binary), "--elapsed-ns", str(elapsed), "--", "/bin/true"],
                check=False,
            )
            self.assertEqual(result.returncode, 0)
            self.assertGreater(int(elapsed.read_text()), 0)

            timed = subprocess.run(
                [
                    str(binary),
                    "--elapsed-ns",
                    str(elapsed),
                    "--timeout-seconds",
                    "1",
                    "--",
                    "/bin/sleep",
                    "10",
                ],
                check=False,
            )
            self.assertEqual(timed.returncode, 124)
