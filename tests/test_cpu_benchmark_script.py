from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "board" / "nvdla-board-cpu-benchmark"


class CpuBenchmarkScriptTests(unittest.TestCase):
    def test_script_is_valid_posix_shell(self) -> None:
        result = subprocess.run(
            ["sh", "-n", str(SCRIPT)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_uses_standard_onnxruntime_tools(self) -> None:
        text = SCRIPT.read_text(encoding="ascii")
        self.assertIn("onnx_test_runner -e cpu", text)
        self.assertIn("onnxruntime_perf_test", text)
        self.assertIn('-x "$THREADS" -y 1', text)
        self.assertIn('--cpu-mask "$CPU_MASK"', text)
        self.assertIn("built_in_warmup_inferences=1", text)

    def test_selects_onnx_or_ort_without_changing_the_default(self) -> None:
        text = SCRIPT.read_text(encoding="ascii")
        self.assertIn("MODEL_FORMAT=onnx", text)
        self.assertIn("--model-format {onnx|ort}", text)
        self.assertIn('MODEL_PATH="$WORKLOAD/model.$MODEL_FORMAT"', text)
        self.assertIn('echo "model_format=$MODEL_FORMAT"', text)

    def test_discovers_cpu_count_without_requiring_getconf(self) -> None:
        text = SCRIPT.read_text(encoding="ascii")
        sysfs = text.index("/sys/devices/system/cpu/online")
        getconf = text.index("command -v getconf")
        cpuinfo = text.index("/proc/cpuinfo")
        self.assertLess(sysfs, getconf)
        self.assertLess(getconf, cpuinfo)
        self.assertIn('split($i, range, "-")', text)

    def test_correctness_brackets_measurement(self) -> None:
        text = SCRIPT.read_text(encoding="ascii")
        before = text.index("run_correctness before")
        steady = text.index("run_perf steady")
        after = text.index("run_correctness after")
        self.assertLess(before, steady)
        self.assertLess(steady, after)
        self.assertIn("correctness_tolerance_absolute=1e-5", text)

    def test_records_time_status_without_enforcing_synchronization(self) -> None:
        text = SCRIPT.read_text(encoding="ascii")
        self.assertIn("NTPSynchronized", text)
        self.assertNotIn("wait_for_time_sync", text)
        self.assertNotIn("time-sync-unverified", text)

    def test_requires_fixed_userspace_cpu_frequency_without_changing_it(self) -> None:
        text = SCRIPT.read_text(encoding="ascii")
        self.assertIn("CPU_NOMINAL_FREQUENCY_KHZ=1200000", text)
        self.assertIn('[ "$value" = userspace ] || return 1', text)
        self.assertIn("cpu_frequency_policy=fixed-verified", text)
        self.assertNotIn("printf 'performance", text)
        self.assertNotIn("scaling_setspeed", text)

    def test_power_sampling_is_concurrent_with_steady_run(self) -> None:
        text = SCRIPT.read_text(encoding="ascii")
        start = text.index("start_power ||")
        steady = text.index("run_perf steady")
        stop = text.index("stop_power; then")
        self.assertLess(start, steady)
        self.assertLess(steady, stop)


if __name__ == "__main__":
    unittest.main()
