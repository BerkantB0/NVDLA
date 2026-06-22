from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

from .common import read_json, run_command, sha256_file, write_json


def _docker_image_id(image: str) -> str | None:
    if not shutil.which("docker"):
        return None
    cp = run_command(["docker", "image", "inspect", image, "--format", "{{.Id}}"], timeout=15)
    if cp.returncode != 0:
        return None
    return cp.stdout.strip()


def _petalinux_metadata(install_dir: Path) -> dict[str, str]:
    path = install_dir / ".version-history"
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip()
        elif "=" in line:
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip().strip('"')
    return data


def check_lock(lock_path: Path, xsa_path: Path) -> dict[str, Any]:
    lock = read_json(lock_path)
    errors: list[str] = []
    warnings: list[str] = []

    actual_sha = sha256_file(xsa_path)
    expected_sha = lock["hardware"]["xsa"]["sha256"]
    if actual_sha != expected_sha:
        errors.append(f"XSA sha256 expected {expected_sha}, got {actual_sha}")

    docker_results = {}
    for name, item in lock.get("docker", {}).items():
        actual = _docker_image_id(item["image"])
        docker_results[name] = {"image": item["image"], "expected": item["image_id"], "actual": actual}
        if actual is None:
            errors.append(f"Docker image {item['image']} is not available")
        elif actual != item["image_id"]:
            errors.append(f"Docker image {item['image']} expected {item['image_id']}, got {actual}")

    install = Path(lock["petalinux"]["install_dir"])
    if not install.exists():
        errors.append(f"PetaLinux install dir not found: {install}")
        pl_meta = {}
    else:
        pl_meta = _petalinux_metadata(install)
        expected_rev = lock["petalinux"]["metadata_revision"]
        actual_rev = pl_meta.get("Metadata Revision")
        if actual_rev and actual_rev != expected_rev:
            errors.append(f"PetaLinux metadata expected {expected_rev}, got {actual_rev}")
        elif not actual_rev:
            warnings.append("PetaLinux metadata revision not found in .version-history")

    return {
        "status": "fail" if errors else "pass",
        "errors": errors,
        "warnings": warnings,
        "xsa": {"path": str(xsa_path), "sha256": actual_sha},
        "docker": docker_results,
        "petalinux": {"install_dir": str(install), "metadata": pl_meta},
    }


def run_lock_check(lock_path: Path, xsa_path: Path, out_path: Path | None) -> int:
    try:
        result = check_lock(lock_path, xsa_path)
        if out_path:
            write_json(out_path, result)
        for warning in result["warnings"]:
            print(f"WARNING: {warning}", file=sys.stderr)
        if result["errors"]:
            for error in result["errors"]:
                print(f"ERROR: {error}", file=sys.stderr)
            return 1
        print("Lock check passed")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

