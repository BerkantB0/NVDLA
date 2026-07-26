from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "board" / "nvdla-board-workload"
CHECK_SCRIPT = ROOT / "tools" / "board" / "nvdla-board-check"
VP_BACKGROUND_SCRIPT = ROOT / "scripts" / "vp_resnet50_background.sh"
VP_CONTROL_SCRIPT = ROOT / "scripts" / "run_modern_lenet_full_control.sh"


class BoardWorkloadScriptTests(unittest.TestCase):
    def test_runner_has_staged_safety_controls(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("run_with_watchdog", text)
        self.assertIn("payload-verification-failure", text)
        self.assertIn("diagnostic-pass-oracle-inconclusive", text)
        self.assertIn("partial-operation-sequence", text)
        self.assertIn('FIRST_FAILURE="$index"', text)
        self.assertIn('if [ "$MODE" = "resnet50" ]; then', text)
        self.assertIn("RUNTIME_TIMEOUT=180", text)
        self.assertIn("stop_server_bounded", text)
        self.assertIn("runtime-server-unreaped.txt", text)
        self.assertIn("detected stalls", text)
        self.assertIn("Timeout waiting for hardware", text)
        self.assertIn("tolower($0) ~ /nvdla/", text)
        self.assertIn("/sys/bus/platform/devices/a0000000.*", text)
        self.assertIn("nvdla-board-workload: $MODE repeat-$index begin", text)
        self.assertIn("repeat_marker_written", text)
        self.assertIn('grep -Fq "$repeat_marker"', text)
        self.assertIn("class_index = count++", text)
        self.assertNotRegex(text, r"(?m)^[ \t]+index = count\+\+$")
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

    def test_resnet_vp_job_is_detached_and_observable(self) -> None:
        background = VP_BACKGROUND_SCRIPT.read_text(encoding="utf-8")
        control = VP_CONTROL_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("nohup env", background)
        self.assertIn("VP_TIMEOUT:-604800", background)
        self.assertIn("nvdla-vp-modern-small-extmem-pool.dtb", background)
        self.assertIn("NVDLA_VP_PROGRESS=", background)
        self.assertIn("golden-candidate", control)
        self.assertIn('WORKLOAD_KIND="${WORKLOAD_KIND:-lenet}"', control)
        self.assertIn('PAYLOAD_IMAGE_NAME="$(basename "$IMAGE")"', control)
        self.assertIn('--image "/mnt/r/$IMAGE_NAME"', control)
        self.assertIn("Unknown image type", control)
        self.assertIn('"$OUT/runtime-output/runtime.log"', control)

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

        background_result = subprocess.run(
            ["dash", "-n", str(VP_BACKGROUND_SCRIPT)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(background_result.returncode, 0, background_result.stderr)
