from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_cpu_board_benchmark.sh"


class CpuHostRunnerTests(unittest.TestCase):
    def test_script_is_valid_bash(self) -> None:
        result = subprocess.run(
            ["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_runner_keeps_one_model_per_boot_boundary(self) -> None:
        text = SCRIPT.read_text(encoding="ascii")
        self.assertIn("cpu-board-last-boot-id", text)
        self.assertIn("refusing to reuse Linux boot", text)
        self.assertIn("nvdla-board-cpu-benchmark", text)
        self.assertIn("--precision fp32 --threads 4 --regime all", text)
        self.assertIn("readlink -f /tmp/nvdla-board-cpu-benchmark-latest.tar.gz", text)
        self.assertNotIn('"$TARGET" reboot', text)

    def test_runner_uses_documented_test_credential(self) -> None:
        text = SCRIPT.read_text(encoding="ascii")
        self.assertIn("NVDLA_BOARD_PASSWORD:-nvdla", text)
        self.assertIn("sshpass -e ssh", text)
        self.assertIn("sshpass -e scp", text)


if __name__ == "__main__":
    unittest.main()
