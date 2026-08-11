from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_benchmark_campaign.py"
SPEC = importlib.util.spec_from_file_location("benchmark_campaign", SCRIPT)
assert SPEC and SPEC.loader
CAMPAIGN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CAMPAIGN)


class BenchmarkCampaignTests(unittest.TestCase):
    def test_cpu_presets_match_final_campaign(self) -> None:
        options = CAMPAIGN.benchmark_options("cpu", "lenet", "latency", 2, "int8")
        self.assertEqual(options[:4], ["--precision", "int8", "--threads", "2"])
        self.assertIn("all", options)
        self.assertIn("200", options)

    def test_nvdla_power_preset_keeps_sampler_affinity(self) -> None:
        options = CAMPAIGN.benchmark_options("nvdla", "resnet50", "power", 4, "fp32")
        self.assertIn("--warmups", options)
        self.assertIn("--power", options)
        self.assertIn("--power-sampler-cpu", options)
        self.assertIn("--benchmark-cpu", options)

    def test_scaling_results_have_separate_directory(self) -> None:
        output = CAMPAIGN.output_directory("cpu", "lenet", "power", 1, "fp32")
        self.assertEqual(
            output.relative_to(ROOT).as_posix(),
            "artifacts/final/cpu-scaling/1t/fp32/lenet/power",
        )


if __name__ == "__main__":
    unittest.main()
