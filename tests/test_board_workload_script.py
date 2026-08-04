from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "board" / "nvdla-board-workload"
BENCHMARK_SCRIPT = ROOT / "tools" / "board" / "nvdla-board-benchmark"
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
        self.assertIn("/sys/module/opendla/parameters/firmware_log", text)

    def test_benchmark_has_controlled_measurement_boundaries(self) -> None:
        text = BENCHMARK_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("--regime {all|cold|warm|steady}", text)
        self.assertIn("--cold-starts N", text)
        self.assertIn("--warm-starts N", text)
        self.assertIn("--steady-samples N", text)
        self.assertIn("--benchmark-cpu {N|none}", text)
        self.assertIn("--power", text)
        self.assertIn("Sample power during the steady phase", text)
        self.assertIn("power tuning options require --power", text)
        self.assertIn("--power requires a regime that includes steady", text)
        self.assertIn("option specified more than once", text)
        self.assertIn("nvdla-benchmark-launch", text)
        self.assertIn("--profile-json profile.json", text)
        self.assertIn("--warmup", text)
        self.assertIn("--iterations", text)
        self.assertIn("drop_caches", text)
        self.assertIn("firmware_log", text)
        self.assertIn("dmesg -n 3", text)
        self.assertIn("verified-xsa-rate", text)
        self.assertIn("CLOCK_EXPECTED_HZ", text)
        self.assertIn("outputs_consistent", text)
        self.assertIn("golden-output.dimg", text)
        self.assertIn("BENCH_CPU=2", text)
        self.assertIn("--rusage rusage.env", text)
        self.assertIn("--interval launch-interval.env", text)
        self.assertIn('--cpu "$BENCH_CPU"', text)
        self.assertIn("nvdla-power-sampler", text)
        self.assertIn('--ready-file "$SAMPLER_READY"', text)
        self.assertIn("POWER_SAMPLER_CPU=3", text)
        self.assertIn("POWER_INTERVAL_MS=50", text)
        self.assertIn('run_profile steady 1 "$WARMUPS" "$STEADY_SAMPLES"', text)
        self.assertNotIn("run_profile power", text)
        self.assertNotIn("POWER_ITERATIONS", text)
        self.assertNotIn('REGIME="${REGIME:-all}"', text)
        self.assertNotIn('POWER_SAMPLE="${POWER_SAMPLE:-0}"', text)
        self.assertIn("NTPSynchronized", text)
        self.assertNotIn("wait_for_time_sync", text)
        self.assertNotIn("time-sync-unverified", text)
        self.assertIn("/proc/sys/kernel/random/boot_id", text)
        self.assertIn("temperature_before_status", text)
        self.assertIn("/sys/bus/iio/devices/iio:device*/in_temp*_input", text)
        self.assertNotIn("/dev/mem", text)
        self.assertNotIn("rmmod", text)

    @unittest.skipUnless(shutil.which("dash"), "dash is required for CLI tests")
    def test_benchmark_cli_help_and_validation(self) -> None:
        help_result = subprocess.run(
            ["dash", str(BENCHMARK_SCRIPT), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("--power", help_result.stdout)
        self.assertIn("--regime", help_result.stdout)

        unknown = subprocess.run(
            ["dash", str(BENCHMARK_SCRIPT), "lenet", "/tmp", "--unknown"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(unknown.returncode, 2)
        self.assertIn("unknown option", unknown.stderr)

        duplicate = subprocess.run(
            [
                "dash",
                str(BENCHMARK_SCRIPT),
                "lenet",
                "/tmp",
                "--regime",
                "steady",
                "--regime",
                "warm",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(duplicate.returncode, 2)
        self.assertIn("option specified more than once", duplicate.stderr)

        unenabled_power = subprocess.run(
            [
                "dash",
                str(BENCHMARK_SCRIPT),
                "lenet",
                "/tmp",
                "--power-idle-seconds",
                "10",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(unenabled_power.returncode, 2)
        self.assertIn("power tuning options require --power", unenabled_power.stderr)

        incompatible_regime = subprocess.run(
            [
                "dash",
                str(BENCHMARK_SCRIPT),
                "lenet",
                "/tmp",
                "--regime",
                "cold",
                "--power",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(incompatible_regime.returncode, 2)
        self.assertIn("requires a regime that includes steady", incompatible_regime.stderr)

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

        benchmark_result = subprocess.run(
            ["dash", "-n", str(BENCHMARK_SCRIPT)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(benchmark_result.returncode, 0, benchmark_result.stderr)

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
