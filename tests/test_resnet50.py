from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from nvdla_test_framework.common import sha256_file, write_json
from nvdla_test_framework.resnet50 import (
    _preprocess_input,
    _verify_sha1,
    promote_resnet50_small_golden,
)


class ResNet50Tests(unittest.TestCase):
    def _golden_fixture(self, root: Path) -> tuple[Path, Path, Path]:
        workload = root / "workload"
        artifact = root / "artifact"
        (artifact / "runtime-output").mkdir(parents=True)
        workload.mkdir()
        output = artifact / "runtime-output" / "output.dimg"
        output.write_text("1 2 3\n")
        (artifact / "serial.log").write_text(
            "".join(
                f"Completed {'Convolution' if index < 114 else 'SDP' if index < 244 else 'PDP'} "
                f"operation index {index} ROI 0\n"
                for index in range(246)
            )
        )
        loadable_hash = "1" * 64
        image_hash = "2" * 64
        lock = root / "lock.json"
        write_json(
            lock,
            {
                "workloads": {
                    "resnet50_imagenet": {
                        "expected_loadable_sha256": loadable_hash,
                        "preprocess": {"expected_output_sha256": image_hash},
                        "expected_nv_small_vp_output_sha256": sha256_file(output),
                        "output_elements": 1000,
                    }
                }
            },
        )
        write_json(
            workload / "generated-manifest.json",
            {
                "loadable": {"size_bytes": 1234},
                "oracle": {"nvdla_exact": {"status": "pending"}},
            },
        )
        write_json(
            artifact / "manifest.json",
            {
                "status": "pass",
                "mode": "resnet50_small_golden",
                "vp_hw_config": "small",
                "vp_runner": "source-docker",
                "output": {"integer_format": True, "elements": 1000},
                "hwl_progress": {"completed": 246, "total": 246},
                "inputs": {
                    "loadable": {"sha256": loadable_hash},
                    "image": {"sha256": image_hash},
                    "dtb": {"sha256": "dtb"},
                    "module": {"sha256": "module"},
                    "runtime": {"sha256": "runtime"},
                },
                "vp_binary": {"sha256": "vp"},
                "vp_cmod": {"sha256": "cmod"},
            },
        )
        return lock, workload, artifact

    def test_promotes_only_verified_nv_small_vp_output(self) -> None:
        with TemporaryDirectory() as temp:
            lock, workload, artifact = self._golden_fixture(Path(temp))
            self.assertEqual(
                promote_resnet50_small_golden(lock, workload, artifact),
                0,
            )
            manifest = json.loads(
                (workload / "generated-manifest.json").read_text()
            )
            self.assertEqual(manifest["oracle"]["nvdla_exact"]["status"], "verified")
            self.assertEqual(manifest["complexity"]["hwl_count"], 246)
            self.assertEqual(
                manifest["complexity"]["operation_counts"],
                {
                    "Convolution": 114,
                    "SDP": 130,
                    "PDP": 2,
                    "CDP": 0,
                    "Rubik": 0,
                    "BDMA": 0,
                },
            )
            self.assertTrue((workload / "golden-output.dimg").is_file())

    def test_rejects_wrong_vp_configuration_during_promotion(self) -> None:
        with TemporaryDirectory() as temp:
            lock, workload, artifact = self._golden_fixture(Path(temp))
            manifest_path = artifact / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["vp_hw_config"] = "full"
            write_json(manifest_path, manifest)
            with self.assertRaisesRegex(ValueError, "hardware_config"):
                promote_resnet50_small_golden(lock, workload, artifact)

    def test_sha1_verification_rejects_wrong_model(self) -> None:
        with TemporaryDirectory() as temp:
            model = Path(temp) / "model"
            model.write_bytes(b"model")
            with self.assertRaisesRegex(ValueError, "sha1 mismatch"):
                _verify_sha1(model, "0" * 40)

    def test_preprocess_resizes_short_side_and_center_crops(self) -> None:
        try:
            from PIL import Image, __version__ as pillow_version
        except ImportError:
            self.skipTest("Pillow is unavailable")

        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.jpg"
            output = root / "output.jpg"
            Image.new("RGB", (480, 360), (20, 40, 60)).save(source, "JPEG")
            result = _preprocess_input(
                source,
                output,
                {
                    "resize_short_side": 256,
                    "crop_width": 224,
                    "crop_height": 224,
                    "format": "JPEG",
                    "quality": 95,
                    "subsampling": 0,
                    "pillow_version": pillow_version,
                },
            )

            with Image.open(output) as image:
                self.assertEqual(image.size, (224, 224))
            self.assertEqual(result["source_size"], [480, 360])
            self.assertEqual(result["resized_size"], [341, 256])
            self.assertEqual(result["crop_box"], [58, 16, 282, 240])
            self.assertEqual(len(result["sha256"]), 64)

    def test_preprocess_rejects_unpinned_pillow(self) -> None:
        try:
            from PIL import Image, __version__ as pillow_version
        except ImportError:
            self.skipTest("Pillow is unavailable")

        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.jpg"
            Image.new("RGB", (224, 224)).save(source, "JPEG")
            with self.assertRaisesRegex(RuntimeError, "required for reproducible"):
                _preprocess_input(
                    source,
                    root / "output.jpg",
                    {
                        "resize_short_side": 256,
                        "crop_width": 224,
                        "crop_height": 224,
                        "format": "JPEG",
                        "quality": 95,
                        "subsampling": 0,
                        "pillow_version": "0.0.invalid",
                    },
                )


if __name__ == "__main__":
    unittest.main()
