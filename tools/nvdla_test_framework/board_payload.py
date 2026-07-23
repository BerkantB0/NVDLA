from __future__ import annotations

import gzip
import shutil
import sys
import tarfile
from pathlib import Path
from typing import Any

from .common import read_json, sha256_file, write_json


EXPECTED_LENET_OUTPUT = "0 2 0 0 0 0 0 124 0 0"
EXPECTED_LENET_OPERATIONS = [
    {"processor": "Convolution", "index": 0},
    {"processor": "SDP", "index": 1},
    {"processor": "PDP", "index": 2},
    {"processor": "Convolution", "index": 3},
    {"processor": "SDP", "index": 4},
    {"processor": "PDP", "index": 5},
    {"processor": "Convolution", "index": 6},
    {"processor": "SDP", "index": 7},
    {"processor": "Convolution", "index": 8},
    {"processor": "SDP", "index": 9},
]


def _verify(path: Path, expected: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"missing workload file: {path}")
    actual = sha256_file(path)
    if actual.upper() != expected.upper():
        raise ValueError(f"sha256 mismatch for {path}: expected {expected}, got {actual}")


def _copy_verified(source: Path, destination: Path, expected: str) -> dict[str, Any]:
    _verify(source, expected)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return {
        "path": destination.as_posix(),
        "sha256": sha256_file(destination),
        "size_bytes": destination.stat().st_size,
    }


def _write_deterministic_tree(source: Path, archive_path: Path) -> None:
    files = sorted(path for path in source.rglob("*") if path.is_file())
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with archive_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for path in files:
                    relative = path.relative_to(source.parent).as_posix()
                    info = archive.gettarinfo(str(path), arcname=relative)
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    info.mode = 0o644
                    with path.open("rb") as input_file:
                        archive.addfile(info, input_file)


def _relative_file_records(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def build_board_payload(
    workloads_dir: Path,
    out_dir: Path,
    archive_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError(f"payload output directory is not empty: {out_dir}")

    sdp_source = workloads_dir / "sdp_regression_small"
    lenet_source = workloads_dir / "lenet_small"
    sdp_manifest = read_json(sdp_source / "generated-manifest.json")
    lenet_manifest = read_json(lenet_source / "generated-manifest.json")

    if sdp_manifest.get("target", {}).get("config") != "nv_small":
        raise ValueError("SDP workload is not tagged nv_small")
    if lenet_manifest.get("target", {}).get("config") != "nv_small":
        raise ValueError("LeNet workload is not tagged nv_small")

    expected_output = " ".join(str(lenet_manifest.get("expected_output", "")).split())
    if expected_output != EXPECTED_LENET_OUTPUT:
        raise ValueError(
            f"unexpected LeNet output vector: expected {EXPECTED_LENET_OUTPUT!r}, got {expected_output!r}"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    sdp_out = out_dir / "sdp_regression_small"
    lenet_out = out_dir / "lenet_small"

    sdp_loadable = sdp_manifest["loadable"]
    sdp_golden = sdp_manifest["golden_outputs"][0]
    sdp_files = {
        "loadable": _copy_verified(
            sdp_source / sdp_loadable["path"],
            sdp_out / "loadable.fbuf",
            sdp_loadable["sha256"],
        ),
        "golden": _copy_verified(
            sdp_source / sdp_golden["path"],
            sdp_out / "golden.dimg",
            sdp_golden["sha256"],
        ),
    }
    sdp_payload_manifest = {
        "schema_version": 1,
        "name": "sdp_regression_small",
        "kind": sdp_manifest["kind"],
        "target": sdp_manifest["target"],
        "upstream_base_sha": sdp_manifest["upstream_base_sha"],
        "source": {
            "nvdla_sw_sha": sdp_manifest.get("source", {}).get("nvdla_sw_sha"),
            "loadable": sdp_manifest.get("source", {}).get("loadable"),
            "golden": sdp_manifest.get("source", {}).get("golden"),
        },
        "loadable": {**sdp_files["loadable"], "path": "loadable.fbuf"},
        "golden": {**sdp_files["golden"], "path": "golden.dimg"},
        "tolerance": {"type": "exact"},
        "known_inconclusive_output": "all-zero dimg payload",
    }
    write_json(sdp_out / "manifest.json", sdp_payload_manifest)

    lenet_loadable = lenet_manifest["loadable"]
    lenet_image = lenet_manifest["image"]
    lenet_files = {
        "loadable": _copy_verified(
            lenet_source / lenet_loadable["path"],
            lenet_out / "loadable.nvdla",
            lenet_loadable["sha256"],
        ),
        "image": _copy_verified(
            lenet_source / lenet_image["path"],
            lenet_out / "input.pgm",
            lenet_image["sha256"],
        ),
    }
    (lenet_out / "expected-output.txt").write_text(expected_output + "\n", encoding="ascii")
    lenet_payload_manifest = {
        "schema_version": 1,
        "name": "lenet_small",
        "kind": lenet_manifest["kind"],
        "target": lenet_manifest["target"],
        "source": {"files": lenet_manifest.get("source", {}).get("files", [])},
        "compiler": {
            key: lenet_manifest.get("compiler", {}).get(key)
            for key in (
                "docker_image",
                "docker_image_id",
                "path",
                "profile",
                "cprecision",
                "configtarget",
                "quantizationMode",
                "informat",
            )
        },
        "loadable": {**lenet_files["loadable"], "path": "loadable.nvdla"},
        "image": {**lenet_files["image"], "path": "input.pgm"},
        "expected_output": expected_output,
        "expected_operations": EXPECTED_LENET_OPERATIONS,
        "tolerance": {"type": "exact"},
    }
    write_json(lenet_out / "manifest.json", lenet_payload_manifest)

    payload_manifest = {
        "schema_version": 1,
        "board": "zcu102",
        "hardware_config": "nv_small",
        "delivery": "sd-fat-read-only",
        "workloads": {
            "sdp": {
                "path": "sdp_regression_small",
                "manifest_sha256": sha256_file(sdp_out / "manifest.json"),
            },
            "lenet": {
                "path": "lenet_small",
                "manifest_sha256": sha256_file(lenet_out / "manifest.json"),
            },
        },
    }
    write_json(out_dir / "PAYLOAD.json", payload_manifest)

    records = _relative_file_records(out_dir)
    # BusyBox sha256sum compares the hexadecimal field case-sensitively.
    sums = "".join(f"{item['sha256'].lower()}  {item['path']}\n" for item in records)
    (out_dir / "SHA256SUMS").write_text(sums, encoding="ascii")
    _write_deterministic_tree(out_dir, archive_path)

    result = {
        **payload_manifest,
        "status": "pass",
        "source": {
            "workloads_dir": str(workloads_dir),
            "sdp_manifest": str(sdp_source / "generated-manifest.json"),
            "lenet_manifest": str(lenet_source / "generated-manifest.json"),
        },
        "payload_dir": str(out_dir),
        "files": _relative_file_records(out_dir),
        "archive": {
            "path": str(archive_path),
            "sha256": sha256_file(archive_path),
        },
    }
    write_json(manifest_path, result)
    return result


def run_board_payload(
    workloads_dir: Path,
    out_dir: Path,
    archive_path: Path,
    manifest_path: Path,
) -> int:
    try:
        result = build_board_payload(workloads_dir, out_dir, archive_path, manifest_path)
    except Exception as exc:
        write_json(
            manifest_path,
            {
                "schema_version": 1,
                "status": "fail",
                "reason": str(exc),
            },
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"NVDLA board payload: {result['payload_dir']}")
    print(f"Archive: {result['archive']['path']}")
    return 0
