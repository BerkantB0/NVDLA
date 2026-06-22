from __future__ import annotations

import json
from pathlib import Path

from .common import sha256_file, write_json


def _write_bytes(path: Path, values: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes((v & 0xFF for v in values)))


def _int8(value: int) -> int:
    value = max(-128, min(127, value))
    return value if value >= 0 else value + 256


def _generate_sdp_passthrough(root: Path) -> dict:
    out = root / "sdp_passthrough"
    values = [0, 1, 2, 3, 4, 5, 6, 7, 120, 127, 128, 129, 250, 251, 252, 255]
    _write_bytes(out / "input.bin", values)
    _write_bytes(out / "golden.bin", values)
    manifest = {
        "schema_version": 1,
        "name": "sdp_passthrough",
        "input_sha256": sha256_file(out / "input.bin"),
        "golden_sha256": sha256_file(out / "golden.bin"),
        "tolerance": {"type": "exact"},
    }
    write_json(out / "generated-manifest.json", manifest)
    return manifest


def _generate_tiny_conv(root: Path) -> dict:
    out = root / "tiny_conv_int8"
    # NCHW 1x1x4x4, deterministic signed int8 interpreted values.
    input_signed = [-8, -7, -6, -5, -1, 0, 1, 2, 3, 4, 5, 6, 10, 11, 12, 13]
    weight = 2
    bias = -3
    golden_signed = [max(-128, min(127, x * weight + bias)) for x in input_signed]
    _write_bytes(out / "input.bin", [_int8(x) for x in input_signed])
    _write_bytes(out / "golden.bin", [_int8(x) for x in golden_signed])
    (out / "model.prototxt").write_text(
        """name: "tiny_conv_int8"
input: "data"
input_shape {
  dim: 1
  dim: 1
  dim: 4
  dim: 4
}
layer {
  name: "conv"
  type: "Convolution"
  bottom: "data"
  top: "conv"
  convolution_param {
    num_output: 1
    kernel_size: 1
    stride: 1
    bias_term: true
  }
}
""",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "name": "tiny_conv_int8",
        "precision": "int8",
        "input_shape_nchw": [1, 1, 4, 4],
        "operation": {"kind": "conv2d", "kernel": [weight], "bias": [bias]},
        "input_sha256": sha256_file(out / "input.bin"),
        "golden_sha256": sha256_file(out / "golden.bin"),
        "prototxt_sha256": sha256_file(out / "model.prototxt"),
        "tolerance": {"type": "absolute", "max_difference": 1},
        "loadable_status": "not_generated",
        "loadable_note": "Generate caffemodel and NVDLA loadable with the pinned compiler before runtime execution.",
    }
    write_json(out / "generated-manifest.json", manifest)
    return manifest


def generate_workloads(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema_version": 1,
        "workloads": [
            _generate_sdp_passthrough(out_dir),
            _generate_tiny_conv(out_dir),
        ],
    }
    write_json(out_dir / "manifest.json", summary)
    print(f"Generated workload artifacts in {out_dir}")
    return 0

