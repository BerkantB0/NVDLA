from __future__ import annotations

import gzip
import io
import json
import struct
import tarfile
import tempfile
import unittest
from pathlib import Path

from nvdla_test_framework.common import sha256_file
from nvdla_test_framework.input_sets import build_multi_image_workloads


class MultiImageInputTests(unittest.TestCase):
    def test_builds_balanced_deterministic_sets(self) -> None:
        try:
            from PIL import Image, __version__ as pillow_version
        except ImportError:
            self.skipTest("Pillow is unavailable")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sources" / "multi"
            source.mkdir(parents=True)

            labels = bytes(digit for digit in range(10) for _ in range(2))
            pixels = b"".join(bytes([digit * 20]) * (28 * 28) for digit in labels)
            with gzip.open(source / "images.gz", "wb") as stream:
                stream.write(struct.pack(">IIII", 2051, 20, 28, 28) + pixels)
            with gzip.open(source / "labels.gz", "wb") as stream:
                stream.write(struct.pack(">II", 2049, 20) + labels)

            archive_path = source / "images.tgz"
            with tarfile.open(archive_path, "w:gz") as archive:
                for digit in range(10):
                    synset = f"class{digit:02d}"
                    for index in range(2):
                        data = io.BytesIO()
                        Image.new("RGB", (180 + index, 160), (digit * 20, index, 50)).save(
                            data, format="JPEG"
                        )
                        payload = data.getvalue()
                        info = tarfile.TarInfo(
                            f"imagenette2-160/val/{synset}/{synset}_{index}.JPEG"
                        )
                        info.size = len(payload)
                        info.mtime = 0
                        archive.addfile(info, io.BytesIO(payload))

            files = [
                {"role": "mnist_images", "name": "images.gz", "url": "unused", "sha256": sha256_file(source / "images.gz")},
                {"role": "mnist_labels", "name": "labels.gz", "url": "unused", "sha256": sha256_file(source / "labels.gz")},
                {"role": "imagenette_archive", "name": "images.tgz", "url": "unused", "sha256": sha256_file(archive_path)},
            ]
            lock = root / "lock.json"
            lock.write_text(
                json.dumps(
                    {
                        "workloads": {
                            "multi_image_inputs": {
                                "source_dir": "multi",
                                "files": files,
                                "resnet50_class_indices": {
                                    f"class{digit:02d}": digit for digit in range(10)
                                },
                            },
                            "resnet50_imagenet": {
                                "preprocess": {
                                    "resize_short_side": 256,
                                    "crop_width": 224,
                                    "crop_height": 224,
                                    "format": "JPEG",
                                    "quality": 95,
                                    "subsampling": 0,
                                    "pillow_version": pillow_version,
                                }
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            out = root / "workloads"
            self.assertEqual(build_multi_image_workloads(lock, root / "sources", out), 0)

            for model in ("lenet", "resnet50"):
                manifest = json.loads(
                    (out / model / "multi20" / "manifest.json").read_text(encoding="utf-8")
                )
                self.assertEqual(manifest["count"], 20)
                self.assertEqual(len(manifest["images"]), 20)
                self.assertEqual(
                    len((out / model / "multi20" / "images.txt").read_text().splitlines()),
                    20,
                )
            labels_out = [
                item["label"]
                for item in json.loads(
                    (out / "lenet" / "multi20" / "manifest.json").read_text()
                )["images"]
            ]
            self.assertEqual(labels_out, [digit for digit in range(10) for _ in range(2)])
            first_lenet = next(
                (out / "lenet" / "multi20" / "images").glob("00-*.pgm")
            ).read_bytes()
            self.assertEqual(first_lenet.split(b"\n", 3)[3][0], 255)


if __name__ == "__main__":
    unittest.main()
