from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

from .common import repo_root, run_command, sha256_file, write_json


NVDLA_SW_BASE_SHA = "79538ba1b52b040a4a4645f630e457fa01839e90"
SDP_REGRESSION_SPECS: dict[str, dict[str, Any]] = {
    "sdp_regression_full": {
        "loadable": Path("regression/flatbufs/kmd/SDP/SDP_X1_L0_0_fbuf"),
        "golden_dir_re": r"^SDP_X1_L0_0_[0-9a-f]+$",
        "target": {
            "config": "nv_full",
            "compatible": ["nvidia,nvdla_os_initial", "nvidia,nv_full"],
        },
    },
    "sdp_regression_small": {
        "loadable": Path("regression/flatbufs/kmd/SDP/SDP_X1_L0_0_small_fbuf"),
        "golden_dir_re": r"^SDP_X1_L0_0_small_[0-9a-f]+$",
        "target": {
            "config": "nv_small",
            "compatible": ["nvidia,nv_small"],
        },
    },
}


def _write_bytes(path: Path, values: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes((v & 0xFF for v in values)))


def _int8(value: int) -> int:
    value = max(-128, min(127, value))
    return value if value >= 0 else value + 256


def _default_nvdla_sw_source() -> Path:
    return Path(os.environ.get("PATCHED_NVDLA_SW", repo_root() / ".work" / "nvdla-sw-patched"))


def _git_sha(path: Path) -> str | None:
    cp = run_command(["git", "-C", str(path), "rev-parse", "HEAD"], timeout=15)
    if cp.returncode != 0:
        return None
    return cp.stdout.strip()


def _first_mismatch(expected: Path, actual: Path) -> int | None:
    with expected.open("rb") as exp, actual.open("rb") as act:
        offset = 0
        while True:
            lhs = exp.read(1024 * 1024)
            rhs = act.read(1024 * 1024)
            if lhs == rhs:
                if not lhs:
                    return None
                offset += len(lhs)
                continue
            for idx, (left, right) in enumerate(zip(lhs, rhs)):
                if left != right:
                    return offset + idx
            return offset + min(len(lhs), len(rhs))


def compare_exact_files(expected: Path, actual: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "tolerance": {"type": "exact"},
        "expected_path": str(expected),
        "actual_path": str(actual),
        "expected_sha256": sha256_file(expected) if expected.is_file() else None,
        "actual_sha256": sha256_file(actual) if actual.is_file() else None,
        "expected_size_bytes": expected.stat().st_size if expected.is_file() else None,
        "actual_size_bytes": actual.stat().st_size if actual.is_file() else None,
    }
    if not expected.is_file() or not actual.is_file():
        result["status"] = "fail"
        result["reason"] = "missing expected or actual file"
        return result
    mismatch = _first_mismatch(expected, actual)
    result["first_mismatch_offset"] = mismatch
    result["status"] = "pass" if mismatch is None else "fail"
    if mismatch is not None:
        result["reason"] = "files differ"
    return result


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


def _find_sdp_golden(source: Path, pattern: str) -> Path:
    golden_root = source / "regression" / "golden"
    matches = sorted(
        path / "dla" / "o_000000.dimg"
        for path in golden_root.iterdir()
        if path.is_dir() and re.fullmatch(pattern, path.name)
    )
    if not matches:
        raise FileNotFoundError(
            f"missing upstream SDP golden matching {pattern} under {golden_root}; run make patch-apply"
        )
    return matches[0]


def _generate_sdp_regression(root: Path, name: str, source_root: Path | None = None) -> dict[str, Any]:
    if name not in SDP_REGRESSION_SPECS:
        raise KeyError(f"unknown SDP regression workload: {name}")

    source = source_root or _default_nvdla_sw_source()
    spec = SDP_REGRESSION_SPECS[name]
    loadable_src = source / spec["loadable"]

    if not loadable_src.is_file():
        raise FileNotFoundError(
            f"missing upstream SDP loadable: {loadable_src}; run make patch-apply or set PATCHED_NVDLA_SW"
        )

    golden_src = _find_sdp_golden(source, spec["golden_dir_re"])
    out = root / name
    loadable = out / "loadable.fbuf"
    golden = out / "golden" / "o_000000.dimg"

    loadable.parent.mkdir(parents=True, exist_ok=True)
    golden.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(loadable_src, loadable)
    shutil.copy2(golden_src, golden)

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "name": name,
        "kind": "upstream_nvdla_flatbuffer_regression",
        "upstream_base_sha": NVDLA_SW_BASE_SHA,
        "target": spec["target"],
        "source": {
            "nvdla_sw": str(source),
            "nvdla_sw_sha": _git_sha(source),
            "loadable": str(loadable_src.relative_to(source)),
            "golden": str(golden_src.relative_to(source)),
        },
        "loadable": {
            "path": "loadable.fbuf",
            "sha256": sha256_file(loadable),
            "size_bytes": loadable.stat().st_size,
        },
        "golden_outputs": [
            {
                "name": "o_000000.dimg",
                "path": "golden/o_000000.dimg",
                "sha256": sha256_file(golden),
                "size_bytes": golden.stat().st_size,
            }
        ],
        "tolerance": {"type": "exact"},
    }
    write_json(out / "generated-manifest.json", manifest)
    return manifest


def _generate_sdp_regression_full(root: Path, source_root: Path | None = None) -> dict[str, Any]:
    return _generate_sdp_regression(root, "sdp_regression_full", source_root)


def _generate_sdp_regression_small(root: Path, source_root: Path | None = None) -> dict[str, Any]:
    return _generate_sdp_regression(root, "sdp_regression_small", source_root)


def generate_workloads(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema_version": 1,
        "workloads": [
            _generate_sdp_passthrough(out_dir),
            _generate_tiny_conv(out_dir),
            _generate_sdp_regression_full(out_dir),
            _generate_sdp_regression_small(out_dir),
        ],
    }
    write_json(out_dir / "manifest.json", summary)
    print(f"Generated workload artifacts in {out_dir}")
    return 0
