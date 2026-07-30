from __future__ import annotations

import os
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
            interval = root / "interval.env"
            rusage = root / "rusage.env"
            affinity = root / "affinity.txt"
            cpu = min(os.sched_getaffinity(0))
            result = subprocess.run(
                [
                    str(binary),
                    "--elapsed-ns",
                    str(elapsed),
                    "--interval",
                    str(interval),
                    "--rusage",
                    str(rusage),
                    "--cpu",
                    str(cpu),
                    "--",
                    "/bin/sh",
                    "-c",
                    f"grep '^Cpus_allowed_list:' /proc/self/status > {affinity}",
                ],
                check=False,
            )
            self.assertEqual(result.returncode, 0)
            self.assertGreater(int(elapsed.read_text()), 0)
            interval_fields = dict(
                line.split("=", 1)
                for line in interval.read_text().splitlines()
            )
            self.assertEqual(interval_fields["clock"], "CLOCK_MONOTONIC_RAW")
            self.assertLess(
                int(interval_fields["start_ns"]),
                int(interval_fields["end_ns"]),
            )
            self.assertEqual(
                int(interval_fields["elapsed_ns"]),
                int(interval_fields["end_ns"])
                - int(interval_fields["start_ns"]),
            )
            self.assertEqual(
                int(interval_fields["elapsed_ns"]),
                int(elapsed.read_text()),
            )
            fields = dict(
                line.split("=", 1)
                for line in rusage.read_text().splitlines()
            )
            self.assertEqual(fields["cpu_affinity"], str(cpu))
            self.assertIn("voluntary_context_switches", fields)
            self.assertIn("involuntary_context_switches", fields)
            self.assertEqual(
                affinity.read_text().split(":", 1)[1].strip(),
                str(cpu),
            )

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
