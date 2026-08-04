from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_board_benchmark.sh"
MAKEFILE = ROOT / "Makefile"


class BoardHostRunnerTests(unittest.TestCase):
    def test_script_is_valid_bash(self) -> None:
        result = subprocess.run(
            ["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_runner_supports_cpu_and_nvdla_gates(self) -> None:
        text = SCRIPT.read_text(encoding="ascii")
        self.assertIn("nvdla-board-cpu-benchmark", text)
        self.assertIn("nvdla-board-benchmark", text)
        self.assertIn("nvdla-board-benchmark-latest.tar.gz", text)
        self.assertIn("${KIND}-board-last-boot-id", text)
        self.assertIn('BENCHMARK_ARGS+=("$1")', text)
        self.assertIn('== *" --power "*', text)
        self.assertNotIn('"$TARGET" reboot', text)

    def test_runner_uses_documented_test_credential(self) -> None:
        text = SCRIPT.read_text(encoding="ascii")
        self.assertIn("PASSWORD=nvdla", text)
        self.assertIn("sshpass -e ssh", text)
        self.assertIn("sshpass -e scp", text)

    def test_collection_is_decoupled_from_make(self) -> None:
        text = MAKEFILE.read_text(encoding="utf-8")
        self.assertNotIn("board-benchmark:", text)

    def test_help_documents_host_and_power_passthrough(self) -> None:
        result = subprocess.run(
            ["bash", str(SCRIPT), "--help"], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--ssh-host", result.stdout)
        self.assertIn("--output", result.stdout)
        self.assertIn("--power", result.stdout)


if __name__ == "__main__":
    unittest.main()
