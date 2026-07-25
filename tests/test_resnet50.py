from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from nvdla_test_framework.resnet50 import _preprocess_input, _verify_sha1


class ResNet50Tests(unittest.TestCase):
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
