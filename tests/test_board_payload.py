from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from nvdla_test_framework.board_payload import EXPECTED_LENET_OUTPUT, build_board_payload
from nvdla_test_framework.common import sha256_file, write_json


class BoardPayloadTests(unittest.TestCase):
    def _workloads(self, root: Path) -> Path:
        workloads = root / "workloads"
        sdp = workloads / "sdp_regression_small"
        lenet = workloads / "lenet_small"
        (sdp / "golden").mkdir(parents=True)
        lenet.mkdir(parents=True)

        (sdp / "loadable.fbuf").write_bytes(b"flatbuffer")
        (sdp / "golden" / "o_000000.dimg").write_bytes(b"dimg-golden")
        write_json(
            sdp / "generated-manifest.json",
            {
                "schema_version": 1,
                "name": "sdp_regression_small",
                "kind": "upstream_nvdla_flatbuffer_regression",
                "upstream_base_sha": "base",
                "target": {"config": "nv_small", "compatible": ["nvidia,nv_small"]},
                "source": {
                    "nvdla_sw_sha": "patched",
                    "loadable": "regression/loadable",
                    "golden": "regression/golden",
                },
                "loadable": {
                    "path": "loadable.fbuf",
                    "sha256": sha256_file(sdp / "loadable.fbuf"),
                },
                "golden_outputs": [
                    {
                        "path": "golden/o_000000.dimg",
                        "sha256": sha256_file(sdp / "golden" / "o_000000.dimg"),
                    }
                ],
            },
        )

        (lenet / "model.nvdla").write_bytes(b"lenet-loadable")
        (lenet / "seven.pgm").write_bytes(b"P5\n1 1\n255\n\x07")
        write_json(
            lenet / "generated-manifest.json",
            {
                "schema_version": 1,
                "name": "lenet_small",
                "kind": "compiled_caffe_lenet_mnist",
                "target": {"config": "nv_small", "compatible": ["nvidia,nv_small"]},
                "source": {"files": [{"name": "model", "sha256": "source"}]},
                "compiler": {
                    "docker_image": "nvdla/vp:latest",
                    "docker_image_id": "sha256:image",
                    "path": "/usr/local/nvdla/nvdla_compiler",
                    "profile": "fast-math",
                    "cprecision": "int8",
                    "configtarget": "nv_small",
                    "quantizationMode": "per-filter",
                    "informat": "nchw",
                },
                "loadable": {
                    "path": "model.nvdla",
                    "sha256": sha256_file(lenet / "model.nvdla"),
                },
                "image": {
                    "path": "seven.pgm",
                    "sha256": sha256_file(lenet / "seven.pgm"),
                },
                "expected_output": EXPECTED_LENET_OUTPUT,
            },
        )
        return workloads

    def test_builds_deterministic_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workloads = self._workloads(root)
            first = build_board_payload(
                workloads,
                root / "first" / "nvdla-tests",
                root / "first.tar.gz",
                root / "first.json",
            )
            second = build_board_payload(
                workloads,
                root / "second" / "nvdla-tests",
                root / "second.tar.gz",
                root / "second.json",
            )

            self.assertEqual(first["archive"]["sha256"], second["archive"]["sha256"])
            self.assertTrue((root / "first" / "nvdla-tests" / "SHA256SUMS").is_file())
            self.assertEqual(
                (root / "first" / "nvdla-tests" / "lenet_small" / "expected-output.txt")
                .read_text()
                .strip(),
                EXPECTED_LENET_OUTPUT,
            )

    def test_rejects_source_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workloads = self._workloads(root)
            (workloads / "sdp_regression_small" / "loadable.fbuf").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "sha256 mismatch"):
                build_board_payload(
                    workloads,
                    root / "out",
                    root / "out.tar.gz",
                    root / "manifest.json",
                )

    def test_rejects_wrong_hardware_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workloads = self._workloads(root)
            path = workloads / "lenet_small" / "generated-manifest.json"
            manifest = json.loads(path.read_text())
            manifest["target"]["config"] = "nv_full"
            write_json(path, manifest)
            with self.assertRaisesRegex(ValueError, "not tagged nv_small"):
                build_board_payload(
                    workloads,
                    root / "out",
                    root / "out.tar.gz",
                    root / "manifest.json",
                )
