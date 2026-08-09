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
        resnet = workloads / "resnet50_small"
        cpu_onnx = workloads / "cpu_onnx"
        input_sets = workloads / "input_sets"
        (sdp / "golden").mkdir(parents=True)
        lenet.mkdir(parents=True)
        resnet.mkdir(parents=True)

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
                    "size_bytes": (lenet / "model.nvdla").stat().st_size,
                },
                "complexity": {
                    "loadable_size_bytes": (lenet / "model.nvdla").stat().st_size,
                    "input_shape_nchw": [1, 1, 28, 28],
                    "output_elements": 10,
                    "hwl_count": 10,
                    "operation_counts": {
                        "Convolution": 4,
                        "SDP": 4,
                        "PDP": 2,
                        "CDP": 0,
                        "Rubik": 0,
                        "BDMA": 0,
                    },
                },
                "image": {
                    "path": "seven.pgm",
                    "sha256": sha256_file(lenet / "seven.pgm"),
                },
                "expected_output": EXPECTED_LENET_OUTPUT,
            },
        )

        (resnet / "model.nvdla").write_bytes(b"resnet-loadable")
        (resnet / "input.jpg").write_bytes(b"jpeg-input")
        (resnet / "golden-output.dimg").write_bytes(b"resnet-golden")
        write_json(
            resnet / "generated-manifest.json",
            {
                "schema_version": 1,
                "name": "resnet50_small",
                "kind": "compiled_caffe_resnet50_imagenet",
                "target": {"config": "nv_small", "compatible": ["nvidia,nv_small"]},
                "model_revision": {"repository": "model", "commit": "revision"},
                "source": {
                    "files": [{"name": "model", "sha256": "source"}],
                    "calibration": {"path": "resnet50.json", "sha256": "calibration"},
                },
                "compiler": {
                    "docker_image": "nvdla/vp:latest",
                    "docker_image_id": "sha256:image",
                    "path": "/usr/local/nvdla/nvdla_compiler",
                    "profile": "fast-math",
                    "cprecision": "int8",
                    "configtarget": "nv_small",
                    "quantizationMode": "per-kernel",
                    "informat": "nchw",
                },
                "loadable": {
                    "path": "model.nvdla",
                    "sha256": sha256_file(resnet / "model.nvdla"),
                    "size_bytes": (resnet / "model.nvdla").stat().st_size,
                },
                "complexity": {
                    "loadable_size_bytes": (resnet / "model.nvdla").stat().st_size,
                    "input_shape_nchw": [1, 3, 224, 224],
                    "output_elements": 1000,
                    "hwl_count": 246,
                    "operation_counts": {
                        "Convolution": 114,
                        "SDP": 130,
                        "PDP": 2,
                        "CDP": 0,
                        "Rubik": 0,
                        "BDMA": 0,
                    },
                },
                "image": {
                    "path": "input.jpg",
                    "sha256": sha256_file(resnet / "input.jpg"),
                    "preprocess": {"output_size": [224, 224]},
                },
                "output_elements": 1000,
                "oracle": {
                    "nvdla_exact": {
                        "status": "verified",
                        "output": {
                            "path": "golden-output.dimg",
                            "sha256": sha256_file(resnet / "golden-output.dimg"),
                        },
                    },
                    "fp32_context": {"status": "context-only", "top5": []},
                },
            },
        )
        for model_name, expected_name in (
            ("lenet", "expected-labels.txt"),
            ("resnet50", "expected-classes.txt"),
        ):
            set_dir = input_sets / model_name / "multi20"
            images = []
            for index in range(20):
                image = set_dir / "images" / f"{index:02d}.jpg"
                image.parent.mkdir(parents=True, exist_ok=True)
                image.write_bytes(f"{model_name}-{index}".encode())
                images.append(
                    {
                        "sequence": index,
                        "path": f"images/{image.name}",
                        "sha256": sha256_file(image),
                    }
                )
            (set_dir / "images.txt").write_text(
                "".join(f"{item['path']}\n" for item in images), encoding="ascii"
            )
            (set_dir / expected_name).write_text(
                "".join(f"{index % 10}\n" for index in range(20)), encoding="ascii"
            )
            write_json(
                set_dir / "manifest.json",
                {
                    "schema_version": 1,
                    "name": "multi20",
                    "model": model_name,
                    "count": 20,
                    "images": images,
                    "image_list_sha256": sha256_file(set_dir / "images.txt"),
                    "expected_indices": {
                        "path": expected_name,
                        "sha256": sha256_file(set_dir / expected_name),
                    },
                },
            )
        cpu_models = []
        for model_name in ("lenet", "resnet50"):
            variants = {}
            for precision in ("fp32", "int8"):
                model_dir = cpu_onnx / model_name / precision
                data_dir = model_dir / "test_data_set_0"
                data_dir.mkdir(parents=True)
                model_path = model_dir / "model.onnx"
                input_path = data_dir / "input_0.pb"
                output_path = data_dir / "output_0.pb"
                model_path.write_bytes(f"{model_name}-{precision}-model".encode())
                input_path.write_bytes(f"{model_name}-input".encode())
                output_path.write_bytes(f"{model_name}-{precision}-output".encode())
                variants[precision] = {
                    "path": "model.onnx",
                    "sha256": sha256_file(model_path),
                    "test_data": {
                        "path": "test_data_set_0",
                        "input": {
                            "path": "input_0.pb",
                            "sha256": sha256_file(input_path),
                        },
                        "output": {
                            "path": "output_0.pb",
                            "sha256": sha256_file(output_path),
                        },
                    },
                }
            cpu_models.append({"name": model_name, "models": variants})
        write_json(
            cpu_onnx / "manifest.json",
            {"schema_version": 1, "status": "pass", "models": cpu_models},
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
            sums_path = root / "first" / "nvdla-tests" / "SHA256SUMS"
            self.assertTrue(sums_path.is_file())
            for line in sums_path.read_text(encoding="ascii").splitlines():
                digest = line.split()[0]
                self.assertEqual(digest, digest.lower())
            self.assertEqual(
                (root / "first" / "nvdla-tests" / "lenet_small" / "expected-output.txt")
                .read_text()
                .strip(),
                EXPECTED_LENET_OUTPUT,
            )
            payload = json.loads(
                (root / "first" / "nvdla-tests" / "PAYLOAD.json").read_text()
            )
            self.assertEqual(payload["schema_version"], 4)
            self.assertEqual(payload["hardware"]["clock"]["expected_hz"], 149985016)
            self.assertEqual(
                payload["hardware"]["clock"]["linux_tolerance_hz"],
                1000,
            )
            self.assertIn("resnet50", payload["workloads"])
            self.assertEqual(
                payload["workloads"]["cpu_onnx"]["models"],
                ["lenet", "resnet50"],
            )
            self.assertTrue(
                (
                    root
                    / "first"
                    / "nvdla-tests"
                    / "cpu_onnx"
                    / "resnet50"
                    / "int8"
                    / "test_data_set_0"
                    / "output_0.pb"
                ).is_file()
            )
            self.assertTrue(
                (root / "first" / "nvdla-tests" / "resnet50_small" / "loadable.nvdla").is_file()
            )
            self.assertTrue(
                (
                    root
                    / "first"
                    / "nvdla-tests"
                    / "lenet_small"
                    / "multi20"
                    / "images"
                    / "00.jpg"
                ).is_file()
            )
            self.assertTrue(
                (
                    root
                    / "first"
                    / "nvdla-tests"
                    / "resnet50_small"
                    / "golden-output.dimg"
                ).is_file()
            )
            sdp_manifest = json.loads(
                (
                    root
                    / "first"
                    / "nvdla-tests"
                    / "sdp_regression_small"
                    / "manifest.json"
                ).read_text()
            )
            self.assertEqual(sdp_manifest["source"]["nvdla_sw_base_sha"], "base")
            self.assertNotIn("nvdla_sw_sha", sdp_manifest["source"])
            lenet_manifest = json.loads(
                (
                    root
                    / "first"
                    / "nvdla-tests"
                    / "lenet_small"
                    / "manifest.json"
                ).read_text()
            )
            self.assertEqual(lenet_manifest["complexity"]["hwl_count"], 10)
            resnet_manifest = json.loads(
                (
                    root
                    / "first"
                    / "nvdla-tests"
                    / "resnet50_small"
                    / "manifest.json"
                ).read_text()
            )
            self.assertEqual(
                resnet_manifest["complexity"]["operation_counts"]["Convolution"],
                114,
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
