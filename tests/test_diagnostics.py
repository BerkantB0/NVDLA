from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nvdla_test_framework.common import read_json
from nvdla_test_framework.diagnostics import classify_sdp_small_diagnostic


class SdpDiagnosticTests(unittest.TestCase):
    def test_accepts_known_sdp_completion_timeout_shape(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            artifact = root / "20260708T000000Z-vp-modern-runtime"
            artifact.mkdir()
            (artifact / "manifest.json").write_text(
                """{
  "run_id": "20260708T000000Z-vp-modern-runtime",
  "status": "fail",
  "modern": {
    "mode": "runtime",
    "status": "fail",
    "probe_config": "nvidia,nv_small",
    "bad_patterns": [],
    "statuses": {
      "module_load": 0,
      "dev_dri": 0
    },
    "workloads": [
      {
        "name": "sdp_regression_small",
        "status": "fail",
        "output_sha256": null,
        "compare": {
          "reason": "missing expected or actual file"
        }
      }
    ]
  }
}
""",
                encoding="utf-8",
            )
            (artifact / "serial.log").write_text(
                "Program SDP operation index 0 ROI 0 Group[0]\n"
                "Enable SDP operation index 0 ROI 0\n"
                "Exit: dla_initiate_processors status=0\n",
                encoding="utf-8",
            )

            rc = classify_sdp_small_diagnostic(root)
            result = read_json(artifact / "sdp-small-diagnostic.json")

            self.assertEqual(rc, 0)
            self.assertEqual(result["classification"], "known_sdp_completion_timeout")

    def test_rejects_unexpected_sdp_completion_timeout_shape(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            artifact = root / "20260708T000000Z-vp-modern-runtime"
            artifact.mkdir()
            (artifact / "manifest.json").write_text(
                """{
  "run_id": "20260708T000000Z-vp-modern-runtime",
  "status": "fail",
  "modern": {
    "mode": "runtime",
    "status": "fail",
    "probe_config": "nvidia,nv_small",
    "bad_patterns": ["DMA-API"],
    "statuses": {
      "module_load": 0,
      "dev_dri": 0
    },
    "workloads": [
      {
        "name": "sdp_regression_small",
        "status": "fail",
        "output_sha256": null,
        "compare": {
          "reason": "missing expected or actual file"
        }
      }
    ]
  }
}
""",
                encoding="utf-8",
            )
            (artifact / "serial.log").write_text(
                "Program SDP operation index 0 ROI 0 Group[0]\n"
                "Enable SDP operation index 0 ROI 0\n"
                "Exit: dla_initiate_processors status=0\n",
                encoding="utf-8",
            )

            rc = classify_sdp_small_diagnostic(root)
            result = read_json(artifact / "sdp-small-diagnostic.json")

            self.assertEqual(rc, 1)
            self.assertEqual(result["classification"], "unexpected_sdp_failure")


if __name__ == "__main__":
    unittest.main()
