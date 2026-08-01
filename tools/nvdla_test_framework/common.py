from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write("\n")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def run_command(command: list[str], timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        timeout=timeout,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def utc_run_id(suffix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{suffix}"


def is_wsl() -> bool:
    try:
        text = Path("/proc/version").read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False
    return "microsoft" in text or bool(os.environ.get("WSL_DISTRO_NAME"))


def docker_backend(image: str) -> tuple[list[str], str, str]:
    candidates = [(["docker"], "native")]
    if is_wsl():
        candidates.append((["docker.exe"], "windows-docker-from-wsl"))
        candidates.append((["cmd.exe", "/c", "docker"], "windows-docker-from-wsl"))
    for prefix, name in candidates:
        try:
            result = run_command(
                [*prefix, "image", "inspect", image, "--format", "{{.Id}}"],
                timeout=30,
            )
        except OSError:
            continue
        image_id = result.stdout.strip()
        if result.returncode == 0 and image_id:
            return prefix, name, image_id
    raise RuntimeError(f"Docker image is unavailable or cannot be inspected: {image}")


def docker_mount_path(path: Path, backend: str) -> str:
    resolved = path.resolve()
    if backend != "windows-docker-from-wsl":
        return str(resolved)
    result = run_command(["wslpath", "-w", str(resolved)], timeout=10)
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"could not convert WSL path for Docker: {resolved}")
    return result.stdout.strip()
