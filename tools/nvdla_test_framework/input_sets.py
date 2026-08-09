from __future__ import annotations

import gzip
import io
import shutil
import struct
import tarfile
import urllib.request
from pathlib import Path
from typing import Any

from .common import read_json, sha256_file, write_json


WORKLOAD = "multi_image_inputs"


def _spec(lock_path: Path) -> dict[str, Any]:
    try:
        return read_json(lock_path)["workloads"][WORKLOAD]
    except KeyError as exc:
        raise KeyError(f"missing workloads.{WORKLOAD} in {lock_path}") from exc


def _verify(path: Path, expected: str) -> str:
    if not path.is_file():
        raise FileNotFoundError(path)
    actual = sha256_file(path)
    if actual.upper() != expected.upper():
        raise ValueError(f"sha256 mismatch for {path}: expected {expected}, got {actual}")
    return actual


def fetch_multi_image_sources(lock_path: Path, sources_dir: Path) -> int:
    spec = _spec(lock_path)
    destination = sources_dir / spec["source_dir"]
    destination.mkdir(parents=True, exist_ok=True)
    records = []
    for item in spec["files"]:
        path = destination / item["name"]
        status = "cached"
        if path.is_file():
            try:
                actual = _verify(path, item["sha256"])
            except ValueError:
                path.unlink()
            else:
                records.append({**item, "sha256": actual, "status": status})
                continue
        print(f"Fetching {item['url']}")
        urllib.request.urlretrieve(item["url"], path)
        actual = _verify(path, item["sha256"])
        records.append({**item, "sha256": actual, "status": "downloaded"})
    write_json(
        destination / "manifest.json",
        {"schema_version": 1, "name": WORKLOAD, "files": records},
    )
    print(f"Multi-image sources ready: {destination}")
    return 0


def _read_mnist(images_path: Path, labels_path: Path) -> tuple[bytes, bytes, int, int]:
    with gzip.open(images_path, "rb") as stream:
        header = stream.read(16)
        if len(header) != 16:
            raise ValueError("truncated MNIST image header")
        magic, count, rows, columns = struct.unpack(">IIII", header)
        pixels = stream.read()
    with gzip.open(labels_path, "rb") as stream:
        header = stream.read(8)
        if len(header) != 8:
            raise ValueError("truncated MNIST label header")
        label_magic, label_count = struct.unpack(">II", header)
        labels = stream.read()
    if magic != 2051 or label_magic != 2049:
        raise ValueError("unexpected MNIST IDX magic")
    if count != label_count or len(labels) != count:
        raise ValueError("inconsistent MNIST image and label counts")
    if len(pixels) != count * rows * columns:
        raise ValueError("truncated MNIST image payload")
    return pixels, labels, rows, columns


def _build_lenet(
    images_path: Path,
    labels_path: Path,
    destination: Path,
    source_indices: dict[str, list[int]],
) -> list[dict[str, Any]]:
    pixels, labels, rows, columns = _read_mnist(
        images_path,
        labels_path,
    )
    selected: list[tuple[int, int]] = []
    for digit in range(10):
        indices = source_indices.get(
            str(digit),
            [index for index, value in enumerate(labels) if value == digit][:2],
        )
        if len(indices) != 2:
            raise ValueError(f"MNIST test set has fewer than two samples for digit {digit}")
        if any(index >= len(labels) or labels[index] != digit for index in indices):
            raise ValueError(f"pinned MNIST indices do not match digit {digit}")
        selected.extend((digit, index) for index in indices)

    images = destination / "images"
    images.mkdir(parents=True, exist_ok=True)
    records = []
    image_size = rows * columns
    for sequence, (digit, index) in enumerate(selected):
        name = f"{sequence:02d}-digit-{digit}-index-{index}.pgm"
        path = images / name
        start = index * image_size
        source_pixels = pixels[start : start + image_size]
        path.write_bytes(
            f"P5\n{columns} {rows}\n255\n".encode("ascii")
            + bytes(255 - value for value in source_pixels)
        )
        records.append(
            {
                "sequence": sequence,
                "label": digit,
                "source_index": index,
                "path": f"images/{name}",
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return records


def _resize_and_crop_jpeg(source: bytes, destination: Path, settings: dict[str, Any]) -> dict[str, Any]:
    try:
        from PIL import Image, __version__ as pillow_version
    except ImportError as exc:
        raise RuntimeError("Pillow is required; on Ubuntu 22.04 install python3-pil") from exc
    expected_version = str(settings["pillow_version"])
    if pillow_version != expected_version:
        raise RuntimeError(f"Pillow {expected_version} is required; found {pillow_version}")
    with Image.open(io.BytesIO(source)) as image:
        image = image.convert("RGB")
        original_size = list(image.size)
        short_side = int(settings["resize_short_side"])
        scale = short_side / min(image.size)
        resized = (int(round(image.width * scale)), int(round(image.height * scale)))
        resampling = getattr(Image, "Resampling", Image)
        image = image.resize(resized, resampling.LANCZOS)
        width = int(settings["crop_width"])
        height = int(settings["crop_height"])
        left = (image.width - width) // 2
        top = (image.height - height) // 2
        image = image.crop((left, top, left + width, top + height))
        image.save(
            destination,
            format=settings["format"],
            quality=int(settings["quality"]),
            subsampling=int(settings["subsampling"]),
            optimize=False,
            progressive=False,
        )
    return {
        "source_size": original_size,
        "resized_size": list(resized),
        "crop_box": [left, top, left + width, top + height],
        "output_size": [width, height],
        "pillow_version": pillow_version,
    }


def _build_resnet(
    archive_path: Path,
    destination: Path,
    classes: dict[str, int],
    settings: dict[str, Any],
    candidate_offsets: dict[str, list[int]],
) -> list[dict[str, Any]]:
    images = destination / "images"
    images.mkdir(parents=True, exist_ok=True)
    records = []
    with tarfile.open(archive_path, "r:gz") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        for synset, class_index in sorted(classes.items(), key=lambda item: item[1]):
            candidates = sorted(
                (
                    member
                    for member in members
                    if f"/val/{synset}/" in member.name
                    and member.name.lower().endswith((".jpeg", ".jpg"))
                ),
                key=lambda member: member.name,
            )
            offsets = candidate_offsets.get(synset, [0, 1])
            if len(offsets) != 2 or len(candidates) <= max(offsets):
                raise ValueError(f"Imagenette has fewer than two validation images for {synset}")
            for member in (candidates[index] for index in offsets):
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError(f"could not read {member.name}")
                sequence = len(records)
                name = f"{sequence:02d}-{synset}-{Path(member.name).stem}.jpg"
                path = images / name
                preprocess = _resize_and_crop_jpeg(stream.read(), path, settings)
                records.append(
                    {
                        "sequence": sequence,
                        "synset": synset,
                        "class_index": class_index,
                        "source_member": member.name,
                        "path": f"images/{name}",
                        "sha256": sha256_file(path),
                        "size_bytes": path.stat().st_size,
                        "preprocess": preprocess,
                    }
                )
    return records


def _write_set(destination: Path, model: str, records: list[dict[str, Any]]) -> None:
    if len(records) != 20:
        raise ValueError(f"{model} input set contains {len(records)} images, expected 20")
    (destination / "images.txt").write_text(
        "".join(f"{record['path']}\n" for record in records),
        encoding="ascii",
    )
    expected_name = "expected-labels.txt" if model == "lenet" else "expected-classes.txt"
    expected_key = "label" if model == "lenet" else "class_index"
    (destination / expected_name).write_text(
        "".join(f"{record[expected_key]}\n" for record in records),
        encoding="ascii",
    )
    write_json(
        destination / "manifest.json",
        {
            "schema_version": 1,
            "name": "multi20",
            "model": model,
            "count": len(records),
            "selection_order": "manifest order; cycles modulo 20",
            "preprocessing": (
                "invert MNIST pixels (255 - source) to the pinned LeNet convention"
                if model == "lenet"
                else "pinned resize, center crop, and JPEG encoding recorded per image"
            ),
            "images": records,
            "image_list_sha256": sha256_file(destination / "images.txt"),
            "expected_indices": {
                "path": expected_name,
                "sha256": sha256_file(destination / expected_name),
                "meaning": "MNIST digit label" if model == "lenet" else "ImageNet class index",
            },
        },
    )


def build_multi_image_workloads(lock_path: Path, sources_dir: Path, out_dir: Path) -> int:
    lock = read_json(lock_path)
    spec = _spec(lock_path)
    source = sources_dir / spec["source_dir"]
    sources_by_role = {item["role"]: source / item["name"] for item in spec["files"]}
    for item in spec["files"]:
        _verify(source / item["name"], item["sha256"])

    stage = out_dir.parent / f".{out_dir.name}.staging"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    try:
        lenet_records = _build_lenet(
            sources_by_role["mnist_images"],
            sources_by_role["mnist_labels"],
            stage / "lenet" / "multi20",
            spec.get("lenet_source_indices", {}),
        )
        resnet_records = _build_resnet(
            sources_by_role["imagenette_archive"],
            stage / "resnet50" / "multi20",
            spec["resnet50_class_indices"],
            lock["workloads"]["resnet50_imagenet"]["preprocess"],
            spec.get("resnet50_candidate_offsets", {}),
        )
        _write_set(stage / "lenet" / "multi20", "lenet", lenet_records)
        _write_set(stage / "resnet50" / "multi20", "resnet50", resnet_records)
        write_json(
            stage / "manifest.json",
            {
                "schema_version": 1,
                "status": "pass",
                "source_files": [
                    {"name": item["name"], "sha256": item["sha256"].lower()}
                    for item in spec["files"]
                ],
                "selection": spec.get("selection", {}),
                "sets": {
                    "lenet": {"path": "lenet/multi20", "count": 20},
                    "resnet50": {"path": "resnet50/multi20", "count": 20},
                },
            },
        )
        if out_dir.exists():
            shutil.rmtree(out_dir)
        stage.rename(out_dir)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    print(f"Multi-image workloads ready: {out_dir}")
    return 0
