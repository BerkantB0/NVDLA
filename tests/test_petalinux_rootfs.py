from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import Any

from nvdla_test_framework.petalinux_rootfs import audit_petalinux_rootfs


RUNTIME_NEEDED = [
    "ld-linux-aarch64.so.1",
    "libc.so.6",
    "libgcc_s.so.1",
    "libnvdla_runtime.so",
    "libstdc++.so.6",
]
LIBRARY_NEEDED = [
    "ld-linux-aarch64.so.1",
    "libc.so.6",
    "libgcc_s.so.1",
    "libm.so.6",
    "libstdc++.so.6",
]
SMOKE_NEEDED = [
    "ld-linux-aarch64.so.1",
    "libc.so.6",
]


class PetaLinuxRootfsTests(unittest.TestCase):
    def _write_archive(self, path: Path, omit: set[str] | None = None) -> None:
        names = {
            "usr/bin/nvdla_runtime",
            "usr/lib/libnvdla_runtime.so",
            "usr/bin/nvdla-kmd-smoke",
            "usr/bin/nvdla-board-check",
            "etc/systemd/system/serial-getty@ttyPS0.service.d/autologin.conf",
            "lib/modules/6.6.10/extra/opendla.ko",
            "lib/ld-linux-aarch64.so.1",
            "usr/lib/libc.so.6",
            "usr/lib/libgcc_s.so.1",
            "usr/lib/libm.so.6",
            "usr/lib/libstdc++.so.6",
        }
        names -= omit or set()
        with tarfile.open(path, "w:gz") as archive:
            for name in sorted(names):
                data = (
                    b"#!/bin/sh\nexit 0\n"
                    if name == "usr/bin/nvdla-board-check"
                    else b"[Service]\nExecStart=-/sbin/agetty --autologin root ttyPS0\n"
                    if name == "etc/systemd/system/serial-getty@ttyPS0.service.d/autologin.conf"
                    else f"synthetic:{name}".encode("ascii")
                )
                member = tarfile.TarInfo(f"./{name}")
                member.size = len(data)
                member.mode = 0o755
                archive.addfile(member, io.BytesIO(data))

    @staticmethod
    def _inspector(
        machine: str = "AArch64",
        runtime_rpaths: list[str] | None = None,
        host_paths: list[str] | None = None,
    ):
        def inspect(path: Path) -> dict[str, Any]:
            needed: list[str] = []
            if path.name == "nvdla_runtime":
                needed = RUNTIME_NEEDED
            elif path.name == "libnvdla_runtime.so":
                needed = LIBRARY_NEEDED
            elif path.name == "nvdla-kmd-smoke":
                needed = SMOKE_NEEDED
            return {
                "machine": machine,
                "needed": needed,
                "rpaths": runtime_rpaths or [] if path.name == "nvdla_runtime" else [],
                "host_paths": host_paths or [],
            }

        return inspect

    def _audit(self, omit: set[str] | None = None, inspector=None) -> dict[str, Any]:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        archive = root / "rootfs.tar.gz"
        self._write_archive(archive, omit)
        return audit_petalinux_rootfs(archive, root / "extract", inspector or self._inspector())

    def test_passes_complete_dynamic_dependency_closure(self) -> None:
        result = self._audit()
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["dependency_closure"]["missing"], [])
        self.assertIn("libnvdla_runtime.so", result["dependency_closure"]["resolved"])

    def test_rejects_missing_runtime_binary(self) -> None:
        result = self._audit({"usr/bin/nvdla_runtime"})
        self.assertEqual(result["status"], "fail")
        self.assertIn("missing runtime from rootfs", result["errors"])

    def test_rejects_missing_runtime_library(self) -> None:
        result = self._audit({"usr/lib/libnvdla_runtime.so"})
        self.assertEqual(result["status"], "fail")
        self.assertIn("missing library from rootfs", result["errors"])

    def test_rejects_missing_smoke_binary(self) -> None:
        result = self._audit({"usr/bin/nvdla-kmd-smoke"})
        self.assertEqual(result["status"], "fail")
        self.assertIn("missing smoke from rootfs", result["errors"])

    def test_rejects_missing_board_collector(self) -> None:
        result = self._audit({"usr/bin/nvdla-board-check"})
        self.assertEqual(result["status"], "fail")
        self.assertIn("missing collector from rootfs", result["errors"])

    def test_rejects_missing_serial_autologin_override(self) -> None:
        result = self._audit({"etc/systemd/system/serial-getty@ttyPS0.service.d/autologin.conf"})
        self.assertEqual(result["status"], "fail")
        self.assertIn("missing serial_autologin from rootfs", result["errors"])

    def test_rejects_wrong_elf_architecture(self) -> None:
        result = self._audit(inspector=self._inspector(machine="Advanced Micro Devices X86-64"))
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("unexpected ELF machine" in error for error in result["errors"]))

    def test_rejects_unsafe_runtime_rpath(self) -> None:
        result = self._audit(inspector=self._inspector(runtime_rpaths=["."]))
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("RPATH/RUNPATH" in error for error in result["errors"]))

    def test_rejects_missing_dynamic_dependency(self) -> None:
        result = self._audit({"usr/lib/libstdc++.so.6"})
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["dependency_closure"]["missing"], ["libstdc++.so.6"])

    def test_rejects_host_build_paths(self) -> None:
        result = self._audit(inspector=self._inspector(host_paths=["/home/user/build/nvdla"]))
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("host build paths" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
