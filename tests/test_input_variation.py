from __future__ import annotations

import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from nvdla_test_framework.input_variation import import_input_variation_archives


class InputVariationTests(unittest.TestCase):
    def test_imports_balanced_multi_input_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "artifact"
            run = artifact / "steady-1"
            run.mkdir(parents=True)
            (artifact / "benchmark.env").write_text(
                "\n".join(
                    [
                        "status=0",
                        "classification=output-stable-input-variation-pass",
                        "input_set=multi20",
                        "model=lenet",
                        "boot_id=boot-one",
                        "nvdla_clock_actual_hz=100000000",
                        "benchmark_cpu=2",
                    ]
                )
                + "\n"
            )
            (artifact / "bad-kernel-patterns.txt").write_text("")
            (artifact / "irq-delta.txt").write_text("40\n")
            (artifact / "uname.txt").write_text("Linux board 6.6.0 #1 SMP aarch64\n")
            (artifact / "software-hashes.txt").write_text(
                "a" * 64 + "  /usr/bin/nvdla_runtime\n"
                + "b" * 64
                + "  /usr/lib/libnvdla_runtime.so\n"
                + "c" * 64
                + "  /payload/SHA256SUMS\n"
            )
            (artifact / "module-hash.txt").write_text(
                "d" * 64 + "  /lib/modules/opendla.ko\n"
            )
            (artifact / "workload-manifest.json").write_text(
                json.dumps({"loadable": {"sha256": "e" * 64}})
            )
            (artifact / "input-set-manifest.json").write_text(
                json.dumps({"name": "multi20", "count": 20})
            )
            (run / "verification.txt").write_text("stable-output-pass\n")
            (run / "input-results.csv").write_text(
                "input_index,expected_index,predicted_index,acceptance,classification_match,output_sha256\n"
                + "".join(
                    f"{index},{index % 10},{index % 10},top1,{int(index != 19)},{'f' * 64}\n"
                    for index in range(20)
                )
            )
            (run / "profile.json").write_text(
                json.dumps(
                    {
                        "schema_version": 3,
                        "clock": "CLOCK_MONOTONIC_RAW",
                        "clock_resolution_ns": 1,
                        "clock_pair_overhead_ns": 1,
                        "input_count": 20,
                        "outputs_consistent": True,
                        "status": 0,
                        "samples": [
                            {
                                "index": index + 1,
                                "input_index": index,
                                "warmup": False,
                                "input_update_ns": 100 + index,
                                "runtime_execution_ns": 1_000_000 + index * 1000,
                                "output_extract_ns": 1000,
                            }
                            for index in range(20)
                        ],
                    }
                )
            )
            archive = root / "artifact.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                bundle.add(artifact, arcname="artifact")

            out = root / "report"
            self.assertEqual(import_input_variation_archives([archive], out), 0)
            summary = json.loads((out / "input-variation-summary.json").read_text())
            self.assertEqual(summary["status"], "pass")
            self.assertEqual(len(summary["per_input"]), 20)
            self.assertEqual(summary["runtime_execution"]["count"], 20)
            self.assertEqual(summary["classification"]["matches"], 19)
            self.assertEqual(summary["classification"]["accuracy_percent"], 95.0)
            self.assertTrue((out / "input-variation-raw.csv").is_file())


if __name__ == "__main__":
    unittest.main()
