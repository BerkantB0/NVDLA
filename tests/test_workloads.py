from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nvdla_test_framework.common import sha256_file
from nvdla_test_framework.workloads import (
    _generate_sdp_regression_full,
    _generate_sdp_regression_small,
    compare_exact_files,
)


class WorkloadGenerationTests(unittest.TestCase):
    def test_sdp_regression_small_copies_pinned_sources(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "nvdla-sw"
            loadable = source / "regression" / "flatbufs" / "kmd" / "SDP" / "SDP_X1_L0_0_small_fbuf"
            golden = source / "regression" / "golden" / "SDP_X1_L0_0_small_c9894d" / "dla" / "o_000000.dimg"
            loadable.parent.mkdir(parents=True)
            golden.parent.mkdir(parents=True)
            loadable.write_bytes(b"flatbuffer")
            golden.write_bytes(b"golden")

            out = root / "artifacts"
            manifest = _generate_sdp_regression_small(out, source)

            generated = out / "sdp_regression_small"
            self.assertEqual((generated / "loadable.fbuf").read_bytes(), b"flatbuffer")
            self.assertEqual((generated / "golden" / "o_000000.dimg").read_bytes(), b"golden")
            self.assertEqual(manifest["name"], "sdp_regression_small")
            self.assertEqual(manifest["target"]["config"], "nv_small")
            self.assertEqual(manifest["tolerance"], {"type": "exact"})
            self.assertEqual(manifest["loadable"]["sha256"], sha256_file(generated / "loadable.fbuf"))
            self.assertEqual(
                manifest["golden_outputs"][0]["sha256"],
                sha256_file(generated / "golden" / "o_000000.dimg"),
            )

    def test_sdp_regression_full_ignores_small_and_large_goldens(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "nvdla-sw"
            loadable = source / "regression" / "flatbufs" / "kmd" / "SDP" / "SDP_X1_L0_0_fbuf"
            full = source / "regression" / "golden" / "SDP_X1_L0_0_b9bf63" / "dla" / "o_000000.dimg"
            small = source / "regression" / "golden" / "SDP_X1_L0_0_small_c9894d" / "dla" / "o_000000.dimg"
            large = source / "regression" / "golden" / "SDP_X1_L0_0_large_4486c3" / "dla" / "o_000000.dimg"
            loadable.parent.mkdir(parents=True)
            for path, data in ((full, b"full"), (small, b"small"), (large, b"large")):
                path.parent.mkdir(parents=True)
                path.write_bytes(data)
            loadable.write_bytes(b"flatbuffer-full")

            out = root / "artifacts"
            manifest = _generate_sdp_regression_full(out, source)

            generated = out / "sdp_regression_full"
            self.assertEqual((generated / "loadable.fbuf").read_bytes(), b"flatbuffer-full")
            self.assertEqual((generated / "golden" / "o_000000.dimg").read_bytes(), b"full")
            self.assertEqual(manifest["name"], "sdp_regression_full")
            self.assertEqual(manifest["target"]["config"], "nv_full")
            self.assertIn("nvidia,nvdla_os_initial", manifest["target"]["compatible"])

    def test_compare_exact_files_reports_first_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            expected = root / "expected.bin"
            actual = root / "actual.bin"
            expected.write_bytes(b"abcde")
            actual.write_bytes(b"abxde")

            result = compare_exact_files(expected, actual)

            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["first_mismatch_offset"], 2)

    def test_compare_exact_files_passes_identical_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            expected = root / "expected.bin"
            actual = root / "actual.bin"
            expected.write_bytes(b"abcde")
            actual.write_bytes(b"abcde")

            result = compare_exact_files(expected, actual)

            self.assertEqual(result["status"], "pass")
            self.assertIsNone(result["first_mismatch_offset"])


if __name__ == "__main__":
    unittest.main()
