from __future__ import annotations

import shutil
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any

from .common import sha256_file, write_json


def _parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def _safe_extract(archive_path: Path, destination: Path) -> list[str]:
    extracted: list[str] = []
    with tarfile.open(archive_path, "r:*") as archive:
        for member in archive.getmembers():
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise ValueError(f"unsafe board artifact path: {member.name}")
            if member.issym() or member.islnk():
                raise ValueError(f"links are not allowed in board artifacts: {member.name}")
            target = destination.joinpath(*name.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise ValueError(f"unsupported board artifact member: {member.name}")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"could not read board artifact member: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as output:
                shutil.copyfileobj(source, output)
            extracted.append(member.name)
    return sorted(extracted)


def import_board_artifact(
    archive_path: Path,
    out_dir: Path,
    serial_log: Path | None = None,
) -> dict[str, Any]:
    if not archive_path.is_file():
        raise FileNotFoundError(f"board artifact archive does not exist: {archive_path}")
    extract_dir = out_dir / "board"
    extract_dir.mkdir(parents=True, exist_ok=True)
    members = _safe_extract(archive_path, extract_dir)

    result_files = list(extract_dir.rglob("result.env"))
    if len(result_files) != 1:
        raise ValueError(f"expected one result.env in board artifact, found {len(result_files)}")
    result = _parse_env(result_files[0])
    mode = result.get("mode", "unknown")
    board_status = int(result.get("status", "1"))

    bad_files = list(extract_dir.rglob("bad-kernel-patterns.txt"))
    bad_patterns = []
    if len(bad_files) == 1:
        bad_patterns = [
            line
            for line in bad_files[0].read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip()
        ]

    stored_archive = out_dir / "board-artifact.tar.gz"
    if archive_path.resolve() != stored_archive.resolve():
        shutil.copyfile(archive_path, stored_archive)
    if serial_log:
        if not serial_log.is_file():
            raise FileNotFoundError(f"serial log does not exist: {serial_log}")
        shutil.copyfile(serial_log, out_dir / "serial.log")

    status = "pass" if board_status == 0 and not bad_patterns else "fail"
    manifest = {
        "schema_version": 1,
        "lane": f"petalinux-board-{mode}",
        "mode": mode,
        "status": status,
        "board_status": board_status,
        "timestamp_utc": result.get("timestamp_utc"),
        "archive": {
            "path": str(stored_archive),
            "sha256": sha256_file(stored_archive),
        },
        "serial_log": str(out_dir / "serial.log") if serial_log else None,
        "members": members,
        "bad_kernel_patterns": bad_patterns,
    }
    write_json(out_dir / "manifest.json", manifest)
    return manifest


def run_board_artifact_import(
    archive_path: Path,
    out_dir: Path,
    serial_log: Path | None,
) -> int:
    try:
        manifest = import_board_artifact(archive_path, out_dir, serial_log)
    except Exception as exc:
        write_json(
            out_dir / "manifest.json",
            {
                "schema_version": 1,
                "lane": "petalinux-board-import",
                "status": "fail",
                "reason": str(exc),
            },
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Board artifact import: {manifest['status']}")
    print(f"Manifest: {out_dir / 'manifest.json'}")
    return 0 if manifest["status"] == "pass" else 1
