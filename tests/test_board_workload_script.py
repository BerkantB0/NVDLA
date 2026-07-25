from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "board" / "nvdla-board-workload"
CHECK_SCRIPT = ROOT / "tools" / "board" / "nvdla-board-check"


class BoardWorkloadScriptTests(unittest.TestCase):
    def test_runner_has_staged_safety_controls(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("run_with_watchdog", text)
        self.assertIn("payload-verification-failure", text)
        self.assertIn("diagnostic-pass-oracle-inconclusive", text)
        self.assertIn("partial-operation-sequence", text)
        self.assertIn('FIRST_FAILURE="$index"', text)
        self.assertIn('RUNTIME_TIMEOUT="${RUNTIME_TIMEOUT:-10}"', text)
        self.assertIn("stop_server_bounded", text)
        self.assertIn("runtime-server-unreaped.txt", text)
        self.assertIn("detected stalls", text)
        self.assertIn("Timeout waiting for hardware", text)
        self.assertIn("tolower($0) ~ /nvdla/", text)
        self.assertIn("/sys/bus/platform/devices/a0000000.*", text)
        self.assertIn("nvdla-board-workload: lenet repeat-$index begin", text)
        self.assertIn("repeat_marker_written", text)
        self.assertIn('grep -Fq "$repeat_marker"', text)
        self.assertNotIn("/sys/bus/platform/devices/a0000000.nvdla", text)
        self.assertNotIn("/dev/mem", text)
        self.assertNotIn("rmmod", text)

    def test_board_check_discovers_refined_xsa_node(self) -> None:
        text = CHECK_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('"nvidia,nv_small"', text)
        self.assertIn("/sys/firmware/devicetree/base", text)
        self.assertIn("dt-clock-names.txt", text)
        self.assertIn("dt-clocks.hex", text)
        self.assertNotIn('DT_NODE="/proc/device-tree/nvdla@a0000000"', text)
        self.assertNotIn('PLATFORM_DEVICE="/sys/bus/platform/devices/a0000000.nvdla"', text)

    @unittest.skipUnless(shutil.which("dash"), "dash is required for POSIX syntax validation")
    def test_runner_is_valid_posix_shell(self) -> None:
        result = subprocess.run(
            ["dash", "-n", str(SCRIPT)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        check_result = subprocess.run(
            ["dash", "-n", str(CHECK_SCRIPT)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(check_result.returncode, 0, check_result.stderr)
