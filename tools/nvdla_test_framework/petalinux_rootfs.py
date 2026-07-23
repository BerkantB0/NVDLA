from __future__ import annotations

import re
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .common import run_command, sha256_file, write_json


RUNTIME_MEMBER = "usr/bin/nvdla_runtime"
LIBRARY_MEMBER = "usr/lib/libnvdla_runtime.so"
SMOKE_MEMBER = "usr/bin/nvdla-kmd-smoke"
FLATBUF_CLIENT_MEMBER = "usr/bin/nvdla-flatbuf-client"
COLLECTOR_MEMBER = "usr/bin/nvdla-board-check"
WORKLOAD_RUNNER_MEMBER = "usr/bin/nvdla-board-workload"
AUTOLOGIN_MEMBER = "etc/systemd/system/serial-getty@ttyPS0.service.d/autologin.conf"
NETWORK_MEMBER = "etc/systemd/network/20-nvdla-direct.network"
MODULE_PREFIX = "lib/modules/"
MODULE_SUFFIX = "/extra/opendla.ko"
FORBIDDEN_HOST_PREFIXES = ("/home/", "/mnt/", "/tmp/work/", "/build/tmp/")

ElfInspector = Callable[[Path], dict[str, Any]]


def _normal_member(name: str) -> str:
    while name.startswith("./"):
        name = name[2:]
    return name


def inspect_elf(path: Path) -> dict[str, Any]:
    header = run_command(["readelf", "-h", str(path)])
    if header.returncode != 0:
        raise ValueError(f"readelf -h failed for {path}: {header.stdout.strip()}")
    machine_match = re.search(r"^\s*Machine:\s*(.+?)\s*$", header.stdout, re.MULTILINE)
    if not machine_match:
        raise ValueError(f"readelf did not report a machine for {path}")

    dynamic = run_command(["readelf", "-d", str(path)])
    if dynamic.returncode != 0:
        raise ValueError(f"readelf -d failed for {path}: {dynamic.stdout.strip()}")
    needed = sorted(set(re.findall(r"\(NEEDED\).*?\[(.+?)\]", dynamic.stdout)))
    rpaths = sorted(set(re.findall(r"\((?:RPATH|RUNPATH)\).*?\[(.*?)\]", dynamic.stdout)))

    strings = run_command(["strings", "-a", str(path)])
    host_paths: list[str] = []
    if strings.returncode == 0:
        for line in strings.stdout.splitlines():
            if any(prefix in line for prefix in FORBIDDEN_HOST_PREFIXES):
                host_paths.append(line)

    return {
        "machine": machine_match.group(1),
        "needed": needed,
        "rpaths": rpaths,
        "host_paths": sorted(set(host_paths)),
    }


def audit_petalinux_rootfs(
    rootfs_path: Path,
    extract_dir: Path,
    inspector: ElfInspector = inspect_elf,
) -> dict[str, Any]:
    errors: list[str] = []
    extracted: dict[str, Path] = {}

    with tarfile.open(rootfs_path, "r:*") as archive:
        members = {_normal_member(member.name): member for member in archive.getmembers()}
        module_members = sorted(
            name for name in members if name.startswith(MODULE_PREFIX) and name.endswith(MODULE_SUFFIX)
        )
        selected = {
            "runtime": RUNTIME_MEMBER,
            "library": LIBRARY_MEMBER,
            "smoke": SMOKE_MEMBER,
            "flatbuf_client": FLATBUF_CLIENT_MEMBER,
            "collector": COLLECTOR_MEMBER,
            "workload_runner": WORKLOAD_RUNNER_MEMBER,
            "serial_autologin": AUTOLOGIN_MEMBER,
            "network_profile": NETWORK_MEMBER,
            "module": module_members[0] if module_members else None,
        }

        for label, member_name in selected.items():
            if not member_name or member_name not in members:
                errors.append(f"missing {label} from rootfs")
                continue
            member = members[member_name]
            if not member.isfile():
                errors.append(f"{label} is not a regular file: {member_name}")
                continue
            source = archive.extractfile(member)
            if source is None:
                errors.append(f"could not read {label}: {member_name}")
                continue
            destination = extract_dir / PurePosixPath(member_name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("wb") as output:
                output.write(source.read())
            extracted[label] = destination
            if label in {"collector", "workload_runner"}:
                if member.mode & 0o111 == 0:
                    errors.append(f"{label} is not executable")
                if not destination.read_bytes().startswith(b"#!/bin/sh\n"):
                    errors.append(f"{label} does not use the expected /bin/sh interpreter")
            if label == "serial_autologin":
                text = destination.read_text(encoding="utf-8", errors="replace")
                if "agetty --autologin root" not in text:
                    errors.append("serial autologin override does not select root")
            if label == "network_profile":
                text = destination.read_text(encoding="utf-8", errors="replace")
                required_lines = {
                    "Name=eth0",
                    "MACAddress=02:00:00:50:10:02",
                    "Address=192.168.50.2/24",
                    "DHCP=no",
                }
                missing_lines = sorted(required_lines.difference(text.splitlines()))
                if missing_lines:
                    errors.append(
                        "network profile is missing required settings: "
                        + ", ".join(missing_lines)
                    )

        available_by_name: dict[str, list[str]] = {}
        for name in members:
            available_by_name.setdefault(PurePosixPath(name).name, []).append(name)

    elf: dict[str, dict[str, Any]] = {}
    for label, path in extracted.items():
        if label in {"collector", "workload_runner", "serial_autologin", "network_profile"}:
            continue
        try:
            info = inspector(path)
        except Exception as exc:
            errors.append(f"could not inspect {label}: {exc}")
            continue
        elf[label] = info
        if info.get("machine") != "AArch64":
            errors.append(f"{label} has unexpected ELF machine {info.get('machine')!r}")
        if info.get("rpaths"):
            errors.append(f"{label} contains RPATH/RUNPATH entries: {info['rpaths']!r}")
        if info.get("host_paths"):
            errors.append(f"{label} contains host build paths")

    needed = sorted({dep for info in elf.values() for dep in info.get("needed", [])})
    resolved = {dep: sorted(available_by_name[dep])[0] for dep in needed if dep in available_by_name}
    missing_dependencies = sorted(dep for dep in needed if dep not in available_by_name)
    if missing_dependencies:
        errors.append(f"missing dynamic dependencies: {', '.join(missing_dependencies)}")

    result = {
        "status": "pass" if not errors else "fail",
        "rootfs": {
            "path": str(rootfs_path),
            "sha256": sha256_file(rootfs_path),
        },
        "members": selected,
        "elf": elf,
        "dependency_closure": {
            "needed": needed,
            "resolved": resolved,
            "missing": missing_dependencies,
        },
        "errors": errors,
    }
    return result


def run_petalinux_rootfs_audit(rootfs_path: Path, extract_dir: Path, out_path: Path) -> int:
    try:
        result = audit_petalinux_rootfs(rootfs_path, extract_dir)
    except Exception as exc:
        result = {
            "status": "fail",
            "rootfs": {"path": str(rootfs_path)},
            "members": {},
            "elf": {},
            "dependency_closure": {"needed": [], "resolved": {}, "missing": []},
            "errors": [str(exc)],
        }
    write_json(out_path, result)
    print(f"PetaLinux rootfs audit: {result['status']}")
    print(f"Audit: {out_path}")
    if result["status"] != "pass":
        for error in result["errors"]:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0
