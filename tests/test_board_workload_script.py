from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "board" / "nvdla-board-workload"


class BoardWorkloadScriptTests(unittest.TestCase):
    def test_runner_has_staged_safety_controls(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("run_with_watchdog", text)
        self.assertIn("payload-verification-failure", text)
        self.assertIn("diagnostic-pass-oracle-inconclusive", text)
        self.assertIn("partial-operation-sequence", text)
        self.assertIn('FIRST_FAILURE="$index"', text)
        self.assertNotIn("/dev/mem", text)
        self.assertNotIn("rmmod", text)

    @unittest.skipUnless(shutil.which("dash"), "dash is required for POSIX syntax validation")
    def test_runner_is_valid_posix_shell(self) -> None:
        result = subprocess.run(
            ["dash", "-n", str(SCRIPT)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
