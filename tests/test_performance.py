from __future__ import annotations

import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from nvdla_test_framework.performance import (
    bootstrap_session_medians,
    import_performance_archives,
    percentile,
    summarize_values,
)


class PerformanceTests(unittest.TestCase):
    def _archive(
        self,
        root: Path,
        name: str,
        *,
        runtime_hash: str = "3" * 64,
        outputs_consistent: bool = True,
        clock_pair_overhead_ns: int = 50,
    ) -> Path:
        session = root / name
        session.mkdir()
        (session / "benchmark.env").write_text(
            "\n".join(
                [
                    "schema_version=1",
                    "model=lenet",
                    "status=0",
                    "classification=exact-performance-pass",
                    "firmware_log=0",
                ]
            )
            + "\n"
        )
        (session / "workload-manifest.json").write_text(
            json.dumps(
                {
                    "loadable": {"sha256": "1" * 64},
                    "image": {"sha256": "2" * 64},
                }
            )
        )
        (session / "software-hashes.txt").write_text(
            "\n".join(
                [
                    f"{runtime_hash}  /usr/bin/nvdla_runtime",
                    f"{'4' * 64}  /usr/lib/libnvdla_runtime.so",
                    f"{'5' * 64}  /payload/SHA256SUMS",
                ]
            )
            + "\n"
        )
        (session / "module-hash.txt").write_text(
            f"{'6' * 64}  /lib/modules/6.6.0/extra/opendla.ko\n"
        )
        (session / "uname.txt").write_text(
            "Linux zcu102-nvdla 6.6.40-xilinx-v2024.1 #1 SMP aarch64 GNU/Linux\n"
        )
        (session / "nvdla-clock-lines.txt").write_text(
            "pl0_ref 1 1 0 100000000 0 0\n"
        )
        for regime, launch, submits in (
            ("cold", 12_000_000, [8_000_000]),
            ("warm", 10_000_000, [7_000_000]),
            ("steady", 0, [6_000_000, 6_200_000]),
        ):
            run = session / f"{regime}-1"
            run.mkdir()
            (run / "verification.txt").write_text("exact-pass\n")
            (run / "run.env").write_text("runtime_status=0\nirq_delta=2\n")
            if launch:
                (run / "launch-elapsed-ns.txt").write_text(f"{launch}\n")
            phases = {
                "runtime_create": 100,
                "loadable_read": 200,
                "runtime_load": 300,
                "emu_init": 400,
                "input_setup": 500,
                "output_setup": 600,
                "output_write": 700,
                "buffer_cleanup": 800,
                "emu_stop": 900,
                "runtime_unload": 1000,
                "runtime_destroy": 1100,
                "test_total": 12_000,
                "process_total": 15_000,
            }
            profile = {
                "schema_version": 1,
                "clock": "CLOCK_MONOTONIC_RAW",
                "clock_resolution_ns": 1,
                "clock_pair_overhead_ns": clock_pair_overhead_ns,
                "warmup_iterations": 0,
                "measured_iterations": len(submits),
                "outputs_consistent": outputs_consistent,
                "status": 0,
                "phases_ns": phases,
                "samples": [
                    {
                        "index": index,
                        "warmup": False,
                        "submit_ns": value,
                        "output_extract_ns": 50,
                    }
                    for index, value in enumerate(submits, start=1)
                ],
            }
            (run / "profile.json").write_text(json.dumps(profile))

        archive = root / f"{name}.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(session, arcname=session.name)
        return archive

    def test_statistics_and_bootstrap_are_deterministic(self) -> None:
        values = [10, 20, 30, 40, 100]
        summary = summarize_values(values)
        self.assertEqual(summary["count"], 5)
        self.assertEqual(summary["median_ns"], 30)
        self.assertEqual(summary["p5_ns"], percentile(values, 0.05))
        first = bootstrap_session_medians([10, 20, 30, 40, 50], iterations=1000)
        second = bootstrap_session_medians([10, 20, 30, 40, 50], iterations=1000)
        self.assertEqual(first, second)

    def test_import_writes_academic_evidence_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archives = [
                self._archive(root, "session-a"),
                self._archive(root, "session-b"),
            ]
            output = root / "report"
            self.assertEqual(import_performance_archives(archives, output), 0)
            for name in (
                "performance-raw.csv",
                "performance-summary.json",
                "performance-summary.csv",
                "performance-report.md",
                "latency-distribution.svg",
                "phase-breakdown.svg",
            ):
                self.assertTrue((output / name).is_file(), name)
            summary = json.loads((output / "performance-summary.json").read_text())
            self.assertEqual(summary["session_count"], 2)
            self.assertEqual(summary["regimes"]["steady"]["latency"]["count"], 4)
            self.assertEqual(summary["sessions"][0]["power"]["status"], "unavailable")
            percentages = summary["regimes"]["steady"]["phases"]["percentages"]
            self.assertAlmostEqual(sum(percentages.values()), 100.0)
            self.assertLess(
                summary["regimes"]["steady"]["maximum_clock_overhead_fraction"],
                0.01,
            )

    def test_rejects_mixed_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archives = [
                self._archive(root, "session-a"),
                self._archive(root, "session-b", runtime_hash="9" * 64),
            ]
            with self.assertRaisesRegex(ValueError, "runtime_sha256"):
                import_performance_archives(archives, root / "report")

    def test_rejects_output_inconsistency(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = self._archive(
                root,
                "session-a",
                outputs_consistent=False,
            )
            with self.assertRaisesRegex(ValueError, "outputs were not identical"):
                import_performance_archives([archive], root / "report")

    def test_rejects_material_clock_measurement_overhead(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = self._archive(
                root,
                "session-a",
                clock_pair_overhead_ns=100_000,
            )
            with self.assertRaisesRegex(ValueError, "overhead exceeds 1%"):
                import_performance_archives([archive], root / "report")
