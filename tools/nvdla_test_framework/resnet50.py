from __future__ import annotations

import hashlib
import re
import shutil
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

from .common import (
    docker_backend,
    docker_mount_path,
    read_json,
    repo_root,
    run_command,
    sha256_file,
    write_json,
)


RESNET50_WORKLOAD = "resnet50_imagenet"
COMPILER_CANDIDATES = [
    "/usr/local/nvdla/nvdla_compiler",
    "/usr/local/nvdla/sw/prebuilt/linux/nvdla_compiler",
    "/usr/local/nvdla/sw/prebuilt/x86/nvdla_compiler",
    "/usr/local/nvdla/sw/prebuilt/x86_64-linux/nvdla_compiler",
]


def _spec(lock_path: Path) -> dict[str, Any]:
    lock = read_json(lock_path)
    try:
        return lock["workloads"][RESNET50_WORKLOAD]
    except KeyError as exc:
        raise KeyError(f"missing workloads.{RESNET50_WORKLOAD} in {lock_path}") from exc


def _source_dir(lock_path: Path, sources_dir: Path) -> Path:
    return sources_dir / _spec(lock_path).get("source_dir", "resnet50-msra")


def _verify(path: Path, expected: str) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"missing ResNet-50 file: {path}")
    actual = sha256_file(path)
    if actual.upper() != expected.upper():
        raise ValueError(f"sha256 mismatch for {path}: expected {expected}, got {actual}")
    return actual


def _verify_sha1(path: Path, expected: str) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest().upper()
    if actual != expected.upper():
        raise ValueError(f"sha1 mismatch for {path}: expected {expected}, got {actual}")
    return actual


def fetch_resnet50_sources(lock_path: Path, sources_dir: Path) -> int:
    spec = _spec(lock_path)
    destination = _source_dir(lock_path, sources_dir)
    destination.mkdir(parents=True, exist_ok=True)
    records = []
    for item in spec["files"]:
        path = destination / item["name"]
        status = "cached"
        if path.is_file():
            try:
                actual = _verify(path, item["sha256"])
                if item.get("sha1"):
                    _verify_sha1(path, item["sha1"])
            except ValueError:
                path.unlink()
            else:
                records.append({**item, "sha256": actual, "status": status})
                continue
        print(f"Fetching {item['url']}")
        urllib.request.urlretrieve(item["url"], path)
        actual = _verify(path, item["sha256"])
        if item.get("sha1"):
            _verify_sha1(path, item["sha1"])
        records.append({**item, "sha256": actual, "status": "downloaded"})

    write_json(
        destination / "manifest.json",
        {
            "schema_version": 1,
            "name": RESNET50_WORKLOAD,
            "model_revision": spec["model_revision"],
            "files": records,
        },
    )
    print(f"ResNet-50 sources ready: {destination}")
    return 0


def _preprocess_input(source: Path, destination: Path, settings: dict[str, Any]) -> dict[str, Any]:
    try:
        from PIL import Image, __version__ as pillow_version
    except ImportError as exc:
        raise RuntimeError(
            "Pillow is required; on Ubuntu 22.04 install python3-pil"
        ) from exc

    expected_version = str(settings["pillow_version"])
    if pillow_version != expected_version:
        raise RuntimeError(
            f"Pillow {expected_version} is required for reproducible JPEG output; found {pillow_version}"
        )

    with Image.open(source) as image:
        image = image.convert("RGB")
        original_size = list(image.size)
        short_side = int(settings["resize_short_side"])
        scale = short_side / min(image.size)
        resized = (
            int(round(image.width * scale)),
            int(round(image.height * scale)),
        )
        resampling = getattr(Image, "Resampling", Image)
        image = image.resize(resized, resampling.LANCZOS)
        crop_width = int(settings["crop_width"])
        crop_height = int(settings["crop_height"])
        left = (image.width - crop_width) // 2
        top = (image.height - crop_height) // 2
        image = image.crop((left, top, left + crop_width, top + crop_height))
        destination.parent.mkdir(parents=True, exist_ok=True)
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
        "crop_box": [left, top, left + crop_width, top + crop_height],
        "output_size": [crop_width, crop_height],
        "pillow_version": pillow_version,
        "sha256": sha256_file(destination),
    }


def _fp32_context(
    prototxt: Path,
    model: Path,
    image: Path,
) -> dict[str, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError:
        return {
            "status": "not-generated",
            "reason": "python3-opencv and python3-numpy are not installed",
        }

    net = cv2.dnn.readNetFromCaffe(str(prototxt), str(model))
    pixels = cv2.imread(str(image), cv2.IMREAD_COLOR)
    if pixels is None:
        raise RuntimeError(f"OpenCV could not decode {image}")
    blob = cv2.dnn.blobFromImage(
        pixels,
        scalefactor=1.0,
        size=(224, 224),
        mean=(103.939, 116.779, 123.68),
        swapRB=False,
        crop=False,
    )
    net.setInput(blob)
    output = net.forward().reshape(-1)
    top5 = np.argsort(output)[-5:][::-1]
    return {
        "status": "context-only",
        "framework": "OpenCV DNN",
        "opencv_version": cv2.__version__,
        "preprocessing": {
            "channel_order": "BGR",
            "mean": [103.939, 116.779, 123.68],
            "scale": 1.0,
        },
        "output_elements": int(output.size),
        "top5": [
            {"index": int(index), "score": float(output[index])}
            for index in top5
        ],
        "note": (
            "This FP32 result is contextual only. The NVDLA INT8 runtime compresses "
            "input bytes to [0,127], so it is not an exact tensor oracle."
        ),
    }


def build_resnet50_small_workload(
    lock_path: Path,
    sources_dir: Path,
    nvdla_sw: Path,
    out_dir: Path,
) -> int:
    lock = read_json(lock_path)
    spec = _spec(lock_path)
    source = _source_dir(lock_path, sources_dir)
    for item in spec["files"]:
        _verify(source / item["name"], item["sha256"])
        if item.get("sha1"):
            _verify_sha1(source / item["name"], item["sha1"])

    calibration = nvdla_sw / spec["calibration"]["nvdla_sw_path"]
    _verify(calibration, spec["calibration"]["sha256"])

    image_name = "cat.center-crop-224.jpg"
    out_dir.mkdir(parents=True, exist_ok=True)
    preprocess = _preprocess_input(
        source / "cat.jpg",
        out_dir / image_name,
        spec["preprocess"],
    )
    _verify(
        out_dir / image_name,
        spec["preprocess"]["expected_output_sha256"],
    )

    stage = repo_root() / ".work" / "resnet50-compiler-input"
    stage.mkdir(parents=True, exist_ok=True)
    stage_files = {
        "ResNet-50-deploy.prototxt": source / "ResNet-50-deploy.prototxt",
        "ResNet-50-model.caffemodel": source / "ResNet-50-model.caffemodel",
        "resnet50.json": calibration,
    }
    for name, path in stage_files.items():
        shutil.copy2(path, stage / name)

    image = lock["docker"]["vp_latest"]["image"]
    docker_prefix, backend, image_id = docker_backend(image)
    compiler = spec["compiler"]
    candidate_text = " ".join(COMPILER_CANDIDATES)
    command_text = (
        "set -eu; "
        "rm -rf /work/wisdom.dir /work/fast-math.nvdla /work/output.protobuf; "
        "cd /work; "
        "compiler_bin=''; "
        f"for candidate in {candidate_text}; do "
        "if [ -x \"$candidate\" ]; then compiler_bin=\"$candidate\"; break; fi; "
        "done; "
        "if [ -z \"$compiler_bin\" ]; then echo 'nvdla_compiler not found' >&2; exit 127; fi; "
        "export LD_LIBRARY_PATH=$(dirname \"$compiler_bin\"):/usr/local/nvdla:${LD_LIBRARY_PATH:-}; "
        "echo \"__NVDLA_COMPILER__=$compiler_bin\"; "
        "\"$compiler_bin\" "
        "--prototxt /src/ResNet-50-deploy.prototxt "
        "--caffemodel /src/ResNet-50-model.caffemodel "
        "-o . "
        f"--profile {compiler['profile']} "
        f"--cprecision {compiler['cprecision']} "
        f"--configtarget {compiler['configtarget']} "
        "--calibtable /src/resnet50.json "
        f"--quantizationMode {compiler['quantizationMode']} "
        f"--informat {compiler['informat']}; "
        "mv fast-math.nvdla resnet50.nv_small.nvdla"
    )
    docker_command = [
        *docker_prefix,
        "run",
        "--rm",
        "-e",
        "HOME=/tmp",
        "-v",
        f"{docker_mount_path(stage, backend)}:/src:ro",
        "-v",
        f"{docker_mount_path(out_dir, backend)}:/work",
        "-w",
        "/work",
        image,
        "bash",
        "-lc",
        command_text,
    ]
    result = run_command(docker_command, timeout=1800)
    (out_dir / "compiler.log").write_text(result.stdout, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(
            f"ResNet-50 compiler failed with status {result.returncode}; see {out_dir / 'compiler.log'}"
        )

    loadable = out_dir / "resnet50.nv_small.nvdla"
    loadable_sha = _verify(loadable, spec["expected_loadable_sha256"])
    output_protobuf = out_dir / "output.protobuf"
    _verify(output_protobuf, spec["expected_output_protobuf_sha256"])
    compiler_match = re.search(r"^__NVDLA_COMPILER__=(.+)$", result.stdout, re.MULTILINE)
    compiler_path = compiler_match.group(1).strip() if compiler_match else None

    shutil.copy2(source / "ResNet-50-deploy.prototxt", out_dir)
    shutil.copy2(source / "ResNet-50-model.caffemodel", out_dir)
    shutil.copy2(calibration, out_dir / "resnet50.json")
    fp32_context = _fp32_context(
        out_dir / "ResNet-50-deploy.prototxt",
        out_dir / "ResNet-50-model.caffemodel",
        out_dir / image_name,
    )
    generated = {
        "schema_version": 1,
        "name": "resnet50_small",
        "kind": "compiled_caffe_resnet50_imagenet",
        "model_revision": spec["model_revision"],
        "source": {
            "files": [
                {
                    "name": item["name"],
                    "url": item["url"],
                    "sha256": sha256_file(source / item["name"]),
                }
                for item in spec["files"]
            ],
            "calibration": {
                "nvdla_sw_commit": lock["sources"]["nvdla_sw"]["commit"],
                "path": spec["calibration"]["nvdla_sw_path"],
                "sha256": sha256_file(calibration),
            },
        },
        "compiler": {
            "docker_image": image,
            "docker_image_id": image_id,
            "docker_backend": backend,
            "path": compiler_path,
            "command": command_text,
            **compiler,
        },
        "loadable": {
            "path": loadable.name,
            "sha256": loadable_sha,
            "size_bytes": loadable.stat().st_size,
        },
        "complexity": {
            "loadable_size_bytes": loadable.stat().st_size,
            "input_shape_nchw": [1, 3, 224, 224],
            "output_elements": spec["output_elements"],
            "hwl_count": None,
            "operation_counts": {},
        },
        "output_protobuf": {
            "path": "output.protobuf",
            "sha256": sha256_file(output_protobuf),
            "size_bytes": output_protobuf.stat().st_size,
        },
        "image": {
            "path": image_name,
            "sha256": sha256_file(out_dir / image_name),
            "preprocess": preprocess,
        },
        "output_elements": spec["output_elements"],
        "oracle": {
            "nvdla_exact": {
                "status": "pending",
                "required_source": "verified source-built nv_small VP",
            },
            "fp32_context": fp32_context,
        },
        "target": spec["target"],
    }
    write_json(out_dir / "generated-manifest.json", generated)
    print(f"ResNet-50 nv_small workload ready: {out_dir}")
    print("Exact NVDLA tensor oracle: pending source-built nv_small VP run")
    return 0


def promote_resnet50_small_golden(
    lock_path: Path,
    workload_dir: Path,
    artifact_dir: Path,
) -> int:
    spec = _spec(lock_path)
    workload_manifest_path = workload_dir / "generated-manifest.json"
    vp_manifest_path = artifact_dir / "manifest.json"
    output_path = artifact_dir / "runtime-output" / "output.dimg"
    workload = read_json(workload_manifest_path)
    vp = read_json(vp_manifest_path)

    expected = {
        "loadable": spec["expected_loadable_sha256"],
        "image": spec["preprocess"]["expected_output_sha256"],
        "output": spec["expected_nv_small_vp_output_sha256"],
    }
    checks = {
        "status": vp.get("status") == "pass",
        "mode": vp.get("mode") == "resnet50_small_golden",
        "hardware_config": vp.get("vp_hw_config") == "small",
        "runner": vp.get("vp_runner") == "source-docker",
        "output_format": vp.get("output", {}).get("integer_format") is True,
        "output_elements": vp.get("output", {}).get("elements") == spec["output_elements"],
        "hwl_completion": vp.get("hwl_progress", {}).get("completed")
        == vp.get("hwl_progress", {}).get("total")
        == 246,
        "loadable_hash": vp.get("inputs", {}).get("loadable", {}).get("sha256", "").lower()
        == expected["loadable"].lower(),
        "image_hash": vp.get("inputs", {}).get("image", {}).get("sha256", "").lower()
        == expected["image"].lower(),
        "output_hash": sha256_file(output_path).lower() == expected["output"].lower(),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ValueError(
            "ResNet-50 VP golden promotion failed: " + ", ".join(failed)
        )

    operation_matches = re.findall(
        r"Completed\s+([A-Za-z]+)\s+operation index\s+(\d+)\s+ROI",
        (artifact_dir / "serial.log").read_text(encoding="utf-8", errors="replace"),
    )
    operation_by_index: dict[int, str] = {}
    for processor, index_text in operation_matches:
        index = int(index_text)
        previous = operation_by_index.setdefault(index, processor)
        if previous != processor:
            raise ValueError(
                f"ResNet-50 VP operation {index} has conflicting processors: "
                f"{previous} and {processor}"
            )
    expected_hwl_count = vp["hwl_progress"]["total"]
    if sorted(operation_by_index) != list(range(expected_hwl_count)):
        raise ValueError(
            "ResNet-50 VP operation sequence does not cover every HWL index"
        )
    operation_counts = Counter(operation_by_index.values())
    golden_path = workload_dir / "golden-output.dimg"
    shutil.copyfile(output_path, golden_path)
    workload["complexity"] = {
        "loadable_size_bytes": workload["loadable"]["size_bytes"],
        "input_shape_nchw": [1, 3, 224, 224],
        "output_elements": spec["output_elements"],
        "hwl_count": expected_hwl_count,
        "operation_counts": {
            engine: operation_counts.get(engine, 0)
            for engine in ("Convolution", "SDP", "PDP", "CDP", "Rubik", "BDMA")
        },
    }
    workload["oracle"]["nvdla_exact"] = {
        "status": "verified",
        "source": "source-built nv_small VP",
        "source_artifact": str(artifact_dir),
        "source_manifest_sha256": sha256_file(vp_manifest_path),
        "output": {
            "path": golden_path.name,
            "sha256": sha256_file(golden_path),
            "size_bytes": golden_path.stat().st_size,
            "elements": spec["output_elements"],
        },
        "configuration_proof": {
            "vp_hw_config": vp["vp_hw_config"],
            "vp_runner": vp["vp_runner"],
            "vp_binary_sha256": vp.get("vp_binary", {}).get("sha256"),
            "cmod_sha256": vp.get("vp_cmod", {}).get("sha256"),
            "dtb_sha256": vp.get("inputs", {}).get("dtb", {}).get("sha256"),
            "module_sha256": vp.get("inputs", {}).get("module", {}).get("sha256"),
            "runtime_sha256": vp.get("inputs", {}).get("runtime", {}).get("sha256"),
            "hwl_progress": vp["hwl_progress"],
        },
    }
    write_json(workload_manifest_path, workload)
    print(f"Verified ResNet-50 nv_small golden: {golden_path}")
    return 0
