from __future__ import annotations

import gzip
import shutil
import sys
import tarfile
from pathlib import Path
from typing import Any

from .common import sha256_file, write_json


SD_FILES = {
    "BOOT.BIN": "boot_bin",
    "boot.scr": "boot_script",
    "image.ub": "fit_image",
}


def _write_deterministic_archive(source_dir: Path, archive_path: Path, names: list[str]) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with archive_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for name in sorted(names):
                    path = source_dir / name
                    info = archive.gettarinfo(str(path), arcname=name)
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    info.mode = 0o644
                    with path.open("rb") as source:
                        archive.addfile(info, source)


def build_petalinux_sd_bundle(
    boot_bin: Path,
    boot_script: Path,
    fit_image: Path,
    out_dir: Path,
    archive_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    sources = {
        "BOOT.BIN": boot_bin,
        "boot.scr": boot_script,
        "image.ub": fit_image,
    }
    missing = [str(path) for path in sources.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"missing SD input files: {', '.join(missing)}")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError(f"SD output directory is not empty: {out_dir}")

    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict[str, str]] = {}
    for name, source in sources.items():
        destination = out_dir / name
        shutil.copyfile(source, destination)
        files[name] = {
            "source": str(source),
            "path": str(destination),
            "sha256": sha256_file(destination),
        }

    sums_path = out_dir / "SHA256SUMS"
    sums_path.write_text(
        "".join(f"{files[name]['sha256']}  {name}\n" for name in sorted(files)),
        encoding="ascii",
    )
    internal_manifest = {
        "schema_version": 1,
        "board": "zcu102",
        "boot_mode": "sd-fit-initramfs",
        "copy_to_fat_partition": sorted(files),
        "files": {name: {"sha256": files[name]["sha256"]} for name in sorted(files)},
    }
    internal_path = out_dir / "SD-BUNDLE.json"
    write_json(internal_path, internal_manifest)

    archive_names = [*files, sums_path.name, internal_path.name]
    _write_deterministic_archive(out_dir, archive_path, archive_names)

    result = {
        **internal_manifest,
        "files": files,
        "status": "pass",
        "archive": {
            "path": str(archive_path),
            "sha256": sha256_file(archive_path),
        },
        "bundle_manifest": {
            "path": str(internal_path),
            "sha256": sha256_file(internal_path),
        },
    }
    write_json(manifest_path, result)
    return result


def run_petalinux_sd_bundle(
    boot_bin: Path,
    boot_script: Path,
    fit_image: Path,
    out_dir: Path,
    archive_path: Path,
    manifest_path: Path,
) -> int:
    try:
        result = build_petalinux_sd_bundle(
            boot_bin,
            boot_script,
            fit_image,
            out_dir,
            archive_path,
            manifest_path,
        )
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
    print(f"PetaLinux SD bundle: {result['archive']['path']}")
    print(f"Copy directory: {out_dir}")
    return 0
