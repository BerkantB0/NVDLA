from __future__ import annotations

import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from nvdla_test_framework.cpu_performance import import_cpu_performance_archives


class CpuPerformanceTests(unittest.TestCase):
    def _archive(
        self,
        root: Path,
        name: str,
        boot_id: str,
        threads: int = 4,
        power: bool = False,
        governor: str = "userspace",
        frequency_khz: int = 1_199_999,
        ntp_synchronized: str = "yes",
        model_format: str = "onnx",
    ) -> Path:
        session = root / name
        session.mkdir()
        (session / "benchmark.env").write_text(
            "\n".join(
                [
                    "schema_version=1",
                    "implementation=onnxruntime-cpu-execution-provider",
                    "model=resnet50",
                    "precision=int8",
                    f"model_format={model_format}",
                    f"threads={threads}",
                    f"cpu_affinity_mask=0x{(1 << threads) - 1:x}",
                    f"cpu_governor={governor}",
                    f"cpu_frequency_khz={frequency_khz}",
                    "cpu_frequency_policy=fixed-verified",
                    "regime=all",
                    "status=0",
                    "classification=correctness-qualified-performance-pass",
                    "timestamp_utc=2026-07-31T12:00:00Z",
                    f"boot_id={boot_id}",
                    f"ntp_synchronized={ntp_synchronized}",
                    "steady_samples=2",
                    f"power_sample={1 if power else 0}",
                    "power_interval_ms=50",
                    "power_sampler_cpu=3",
                ]
            )
            + "\n",
            encoding="ascii",
        )
        for phase in ("before", "after"):
            (session / f"correctness-{phase}.status").write_text("pass\n")
        (session / "time-sync.env").write_text(
            f"boot_id={boot_id}\nntp_synchronized={ntp_synchronized}\n"
        )
        (session / "bad-kernel-patterns.txt").write_text("")
        (session / "uname.txt").write_text("Linux board 6.6.10 #1 SMP aarch64\n")
        for phase in ("before", "after"):
            (session / f"governors-{phase}.txt").write_text(
                "".join(
                    f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_governor={governor}\n"
                    for cpu in range(threads)
                )
            )
            (session / f"frequencies-{phase}.txt").write_text(
                "".join(
                    f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_cur_freq={frequency_khz}\n"
                    for cpu in range(threads)
                )
            )
        (session / "software-hashes.txt").write_text(
            "\n".join(
                [
                    "a" * 64 + "  /usr/bin/onnx_test_runner",
                    "b" * 64 + "  /usr/bin/onnxruntime_perf_test",
                    "c" * 64 + "  /usr/lib/libonnxruntime.so.1.18.1",
                    "2" * 64 + "  /usr/bin/nvdla-board-cpu-benchmark",
                    "3" * 64 + "  /usr/bin/nvdla-benchmark-launch",
                    "4" * 64 + "  /usr/bin/nvdla-power-sampler",
                    "d" * 64 + f"  /payload/model.{model_format}",
                    "e" * 64 + "  /payload/input_0.pb",
                    "f" * 64 + "  /payload/output_0.pb",
                    "1" * 64 + "  /payload/SHA256SUMS",
                ]
            )
            + "\n"
        )
        (session / "cpu-workload-manifest.json").write_text(
            json.dumps(
                {
                    "models": [
                        {
                            "name": "resnet50",
                            "models": {
                                "int8": {
                                    "sha256": "d" * 64,
                                    "ort": {
                                        "sha256": "d" * 64,
                                        "size_bytes": 90,
                                        "format": "ORT",
                                        "source_onnx_sha256": "9" * 64,
                                        "optimization_style": "Fixed",
                                        "target_platform": "arm",
                                    },
                                    "size_bytes": 100,
                                    "node_count": 2,
                                    "initializer_count": 1,
                                    "operator_counts": {"Conv": 1, "Relu": 1},
                                    "inputs": [{"name": "input", "shape": [1, 3, 224, 224]}],
                                    "outputs": [{"name": "prob", "shape": [1, 1000]}],
                                    "test_data": {
                                        "input": {"sha256": "e" * 64},
                                        "output": {"sha256": "f" * 64},
                                    },
                                }
                            },
                        }
                    ]
                }
            )
            + "\n"
        )
        for regime, samples in (("cold", [0.2]), ("warm", [0.1]), ("steady", [0.05, 0.06])):
            run = session / f"{regime}-1"
            run.mkdir()
            (run / "result.csv").write_text(
                "".join(f"resnet50,{value},1000,90,{index}\n" for index, value in enumerate(samples))
            )
            (run / "launch-elapsed-ns.txt").write_text(
                f"{300_000_000 if regime == 'cold' else 200_000_000}\n"
            )
            (run / "runtime.stdout.log").write_text(
                "Session creation time cost: 0.02 s\nFirst inference time cost: 80 ms\n"
            )
            (run / "run.env").write_text(f"runtime_status=0\nmeasured_samples={len(samples)}\n")
            (run / "rusage.env").write_text(
                f"cpu_affinity=mask:0x{(1 << threads) - 1:x}\n"
                "user_time_ns=1\nsystem_time_ns=2\nvoluntary_context_switches=3\n"
                "involuntary_context_switches=4\n"
            )
            if regime == "steady" and power:
                (run / "launch-interval.env").write_text(
                    "schema_version=1\nclock=CLOCK_MONOTONIC_RAW\n"
                    "start_ns=1000000000\nend_ns=3000000000\nelapsed_ns=2000000000\n"
                )
        if power:
            power_dir = session / "power-sampling"
            power_dir.mkdir()

            def write_power(path: Path, timestamps: list[int], ps: int, pl: int) -> None:
                lines = ["sample_index,timestamp_ns,domain,rail,power_uw"]
                for index, timestamp in enumerate(timestamps):
                    lines.append(f"{index},{timestamp},PS,VCCPSINTFP,{ps}")
                    lines.append(f"{index},{timestamp},PL,VCCINT,{pl}")
                path.write_text("\n".join(lines) + "\n")

            write_power(power_dir / "idle-readings.csv", [0, 1_000_000_000], 1_000_000, 500_000)
            write_power(
                power_dir / "readings.csv",
                [500_000_000, 1_000_000_000, 2_000_000_000, 3_000_000_000, 3_500_000_000],
                2_000_000,
                1_000_000,
            )
        archive = root / f"{name}.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(session, arcname=session.name)
        return archive

    def test_imports_correctness_qualified_cpu_campaign(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archives = [
                self._archive(root, "session-1", "boot-1"),
                self._archive(root, "session-2", "boot-2"),
            ]
            out = root / "report"
            self.assertEqual(import_cpu_performance_archives(archives, out), 0)
            summary = json.loads((out / "cpu-performance-summary.json").read_text())
            self.assertEqual(summary["status"], "pass")
            self.assertEqual(summary["session_count"], 2)
            self.assertEqual(summary["regimes"]["steady"]["count"], 4)
            self.assertTrue((out / "cpu-performance-report.md").is_file())
            self.assertTrue((out / "cpu-latency-distribution.svg").is_file())

    def test_rejects_mixed_thread_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archives = [
                self._archive(root, "session-1", "boot-1", threads=4),
                self._archive(root, "session-2", "boot-2", threads=1),
            ]
            self.assertEqual(import_cpu_performance_archives(archives, root / "report"), 1)

    def test_rejects_mixed_model_formats(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archives = [
                self._archive(root, "session-1", "boot-1", model_format="onnx"),
                self._archive(root, "session-2", "boot-2", model_format="ort"),
            ]
            self.assertEqual(import_cpu_performance_archives(archives, root / "report"), 1)

    def test_rejects_non_userspace_governor(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = self._archive(root, "session-1", "boot-1", governor="performance")
            self.assertEqual(import_cpu_performance_archives([archive], root / "report"), 1)

    def test_accepts_recorded_unsynchronized_wall_clock(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = self._archive(
                root, "session-1", "boot-1", ntp_synchronized="no"
            )
            out = root / "report"
            self.assertEqual(import_cpu_performance_archives([archive], out), 0)
            summary = json.loads((out / "cpu-performance-summary.json").read_text())
            self.assertEqual(summary["sessions"][0]["ntp_synchronized"], "no")

    def test_rejects_mixed_fixed_frequencies(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archives = [
                self._archive(root, "session-1", "boot-1"),
                self._archive(root, "session-2", "boot-2", frequency_khz=1_000_000),
            ]
            self.assertEqual(import_cpu_performance_archives(archives, root / "report"), 1)

    def test_rejects_frequency_change_within_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = self._archive(root, "session-1", "boot-1")
            session = root / "session-1"
            (session / "frequencies-after.txt").write_text(
                "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq=1000000\n"
            )
            with tarfile.open(archive, "w:gz") as bundle:
                bundle.add(session, arcname=session.name)
            self.assertEqual(import_cpu_performance_archives([archive], root / "report"), 1)

    def test_integrates_ps_and_pl_power_domains(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = self._archive(root, "session-1", "boot-1", power=True)
            out = root / "report"
            self.assertEqual(import_cpu_performance_archives([archive], out), 0)
            summary = json.loads((out / "cpu-performance-summary.json").read_text())
            power = summary["sessions"][0]["power"]
            self.assertEqual(power["status"], "available")
            self.assertAlmostEqual(power["domains"]["PS"]["idle_mean_watts"], 1.0)
            self.assertAlmostEqual(power["domains"]["PL"]["active_mean_watts"], 1.0)
            self.assertEqual(summary["power"]["session_count"], 1)


if __name__ == "__main__":
    unittest.main()
