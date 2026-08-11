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
CPU_TOOL_NEEDED = [
    "ld-linux-aarch64.so.1",
    "libc.so.6",
    "libgcc_s.so.1",
    "libm.so.6",
    "libonnxruntime.so.1.18.1",
    "libstdc++.so.6",
]
CPU_LIBRARY_NEEDED = [
    "libc.so.6",
    "libgcc_s.so.1",
    "libm.so.6",
    "libstdc++.so.6",
]


class PetaLinuxRootfsTests(unittest.TestCase):
    def _write_archive(
        self,
        path: Path,
        omit: set[str] | None = None,
        network_profile: bytes | None = None,
        timesync_profile: bytes | None = None,
    ) -> None:
        names = {
            "usr/bin/nvdla_runtime",
            "usr/lib/libnvdla_runtime.so",
            "usr/bin/nvdla-kmd-smoke",
            "usr/bin/nvdla-flatbuf-client",
            "usr/bin/nvdla-board-check",
            "usr/bin/nvdla-board-workload",
            "usr/bin/nvdla-board-benchmark",
            "usr/bin/nvdla-board-cpu-benchmark",
            "usr/bin/nvdla-benchmark-launch",
            "usr/bin/nvdla-power-sampler",
            "usr/bin/gst-launch-1.0",
            "usr/bin/gst-inspect-1.0",
            "usr/lib/gstreamer-1.0/libgstjpeg.so",
            "usr/lib/gstreamer-1.0/libgstjpegformat.so",
            "usr/lib/gstreamer-1.0/libgstvideoconvert.so",
            "usr/bin/onnx_test_runner",
            "usr/bin/onnxruntime_perf_test",
            "usr/lib/libonnxruntime.so.1.18.1",
            "usr/lib/libonnxruntime.so.1",
            "etc/systemd/system/serial-getty@ttyPS0.service.d/autologin.conf",
            "etc/systemd/network/20-nvdla-direct.network",
            "etc/systemd/timesyncd.conf.d/nvdla-host.conf",
            "etc/systemd/system/sysinit.target.wants/systemd-timesyncd.service",
            "etc/ssh/sshd_config.d/60-nvdla-test.conf",
            "etc/shadow",
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
                if name == "etc/systemd/system/sysinit.target.wants/systemd-timesyncd.service":
                    member = tarfile.TarInfo(f"./{name}")
                    member.type = tarfile.SYMTYPE
                    member.linkname = "/lib/systemd/system/systemd-timesyncd.service"
                    archive.addfile(member)
                    continue
                data = (
                    b"#!/bin/sh\nexit 0\n"
                    if name
                    in {
                        "usr/bin/nvdla-board-check",
                        "usr/bin/nvdla-board-workload",
                        "usr/bin/nvdla-board-benchmark",
                        "usr/bin/nvdla-board-cpu-benchmark",
                    }
                    else b"[Service]\nExecStart=-/sbin/agetty --autologin root ttyPS0\n"
                    if name == "etc/systemd/system/serial-getty@ttyPS0.service.d/autologin.conf"
                    else (
                        network_profile
                        or (
                            b"[Match]\n"
                            b"Name=eth0\n"
                            b"MACAddress=02:00:00:50:10:02\n"
                            b"[Network]\n"
                            b"Address=192.168.50.2/24\n"
                            b"DHCP=no\n"
                        )
                    )
                    if name == "etc/systemd/network/20-nvdla-direct.network"
                    else (
                        timesync_profile
                        or (
                            b"[Time]\n"
                            b"NTP=192.168.50.1\n"
                            b"FallbackNTP=\n"
                            b"RootDistanceMaxSec=30\n"
                        )
                    )
                    if name == "etc/systemd/timesyncd.conf.d/nvdla-host.conf"
                    else (
                        b"PermitRootLogin yes\n"
                        b"PasswordAuthentication yes\n"
                        b"PermitEmptyPasswords no\n"
                    )
                    if name == "etc/ssh/sshd_config.d/60-nvdla-test.conf"
                    else (
                        b"root:$6$nvdlatest$Bt1voKTDGyA6E/Kr.2BRpnPder7XkMw6TzrTWhHAl7ZT/"
                        b"4QwePA2i05NlLe.XMHjw/oVBFznvoIPxc9eF1rBN0:15069:0:99999:7:::\n"
                    )
                    if name == "etc/shadow"
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
        cpu_rpaths: list[str] | None = None,
        host_paths: list[str] | None = None,
    ):
        def inspect(path: Path) -> dict[str, Any]:
            needed: list[str] = []
            if path.name == "nvdla_runtime":
                needed = RUNTIME_NEEDED
            elif path.name == "libnvdla_runtime.so":
                needed = LIBRARY_NEEDED
            elif path.name in {
                "nvdla-kmd-smoke",
                "nvdla-flatbuf-client",
                "nvdla-benchmark-launch",
                "nvdla-power-sampler",
            }:
                needed = SMOKE_NEEDED
            elif path.name in {"onnx_test_runner", "onnxruntime_perf_test"}:
                needed = CPU_TOOL_NEEDED
            elif path.name == "libonnxruntime.so.1.18.1":
                needed = CPU_LIBRARY_NEEDED
            result = {
                "machine": machine,
                "needed": needed,
                "rpaths": runtime_rpaths or [] if path.name == "nvdla_runtime" else [],
                "host_paths": host_paths or [],
            }
            if path.name in {
                "onnx_test_runner",
                "onnxruntime_perf_test",
                "libonnxruntime.so.1.18.1",
            }:
                result["rpaths"] = cpu_rpaths if cpu_rpaths is not None else ["$ORIGIN"]
            return result

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

    def test_rejects_missing_flatbuffer_client(self) -> None:
        result = self._audit({"usr/bin/nvdla-flatbuf-client"})
        self.assertEqual(result["status"], "fail")
        self.assertIn("missing flatbuf_client from rootfs", result["errors"])

    def test_rejects_missing_board_collector(self) -> None:
        result = self._audit({"usr/bin/nvdla-board-check"})
        self.assertEqual(result["status"], "fail")
        self.assertIn("missing collector from rootfs", result["errors"])

    def test_rejects_missing_workload_runner(self) -> None:
        result = self._audit({"usr/bin/nvdla-board-workload"})
        self.assertEqual(result["status"], "fail")
        self.assertIn("missing workload_runner from rootfs", result["errors"])

    def test_rejects_missing_benchmark_runner(self) -> None:
        result = self._audit({"usr/bin/nvdla-board-benchmark"})
        self.assertEqual(result["status"], "fail")
        self.assertIn("missing benchmark_runner from rootfs", result["errors"])

    def test_rejects_missing_cpu_benchmark_runner(self) -> None:
        result = self._audit({"usr/bin/nvdla-board-cpu-benchmark"})
        self.assertEqual(result["status"], "fail")
        self.assertIn("missing cpu_benchmark_runner from rootfs", result["errors"])

    def test_rejects_missing_benchmark_launcher(self) -> None:
        result = self._audit({"usr/bin/nvdla-benchmark-launch"})
        self.assertEqual(result["status"], "fail")
        self.assertIn("missing benchmark_launcher from rootfs", result["errors"])

    def test_rejects_missing_power_sampler(self) -> None:
        result = self._audit({"usr/bin/nvdla-power-sampler"})
        self.assertEqual(result["status"], "fail")
        self.assertIn("missing power_sampler from rootfs", result["errors"])

    def test_rejects_missing_video_pipeline(self) -> None:
        result = self._audit({"usr/bin/gst-launch-1.0"})
        self.assertEqual(result["status"], "fail")
        self.assertIn("missing gst_launch from rootfs", result["errors"])

    def test_rejects_missing_cpu_test_runner(self) -> None:
        result = self._audit({"usr/bin/onnx_test_runner"})
        self.assertEqual(result["status"], "fail")
        self.assertIn("missing cpu_test_runner from rootfs", result["errors"])

    def test_rejects_missing_cpu_performance_tool(self) -> None:
        result = self._audit({"usr/bin/onnxruntime_perf_test"})
        self.assertEqual(result["status"], "fail")
        self.assertIn("missing cpu_perf_test from rootfs", result["errors"])

    def test_rejects_missing_cpu_runtime_library(self) -> None:
        result = self._audit({"usr/lib/libonnxruntime.so.1.18.1"})
        self.assertEqual(result["status"], "fail")
        self.assertIn("missing cpu_library from rootfs", result["errors"])

    def test_rejects_missing_serial_autologin_override(self) -> None:
        result = self._audit({"etc/systemd/system/serial-getty@ttyPS0.service.d/autologin.conf"})
        self.assertEqual(result["status"], "fail")
        self.assertIn("missing serial_autologin from rootfs", result["errors"])

    def test_rejects_missing_static_network_profile(self) -> None:
        result = self._audit({"etc/systemd/network/20-nvdla-direct.network"})
        self.assertEqual(result["status"], "fail")
        self.assertIn("missing network_profile from rootfs", result["errors"])

    def test_rejects_incomplete_static_network_profile(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        archive = root / "rootfs.tar.gz"
        self._write_archive(archive, network_profile=b"[Match]\nName=eth0\n")
        result = audit_petalinux_rootfs(archive, root / "extract", self._inspector())
        self.assertEqual(result["status"], "fail")
        self.assertTrue(
            any("network profile is missing required settings" in error for error in result["errors"])
        )

    def test_rejects_missing_timesync_profile(self) -> None:
        result = self._audit({"etc/systemd/timesyncd.conf.d/nvdla-host.conf"})
        self.assertEqual(result["status"], "fail")
        self.assertIn("missing timesync_profile from rootfs", result["errors"])

    def test_rejects_disabled_timesync_service(self) -> None:
        result = self._audit(
            {"etc/systemd/system/sysinit.target.wants/systemd-timesyncd.service"}
        )
        self.assertIn("systemd-timesyncd is not enabled at boot", result["errors"])

    def test_rejects_missing_test_ssh_policy(self) -> None:
        result = self._audit({"etc/ssh/sshd_config.d/60-nvdla-test.conf"})
        self.assertIn("missing ssh_policy from rootfs", result["errors"])

    def test_rejects_missing_test_root_password(self) -> None:
        result = self._audit({"etc/shadow"})
        self.assertIn("missing shadow from rootfs", result["errors"])

    def test_rejects_incomplete_timesync_profile(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        archive = root / "rootfs.tar.gz"
        self._write_archive(archive, timesync_profile=b"[Time]\nNTP=192.168.50.1\n")
        result = audit_petalinux_rootfs(archive, root / "extract", self._inspector())
        self.assertEqual(result["status"], "fail")
        self.assertTrue(
            any("timesync profile is missing required settings" in error for error in result["errors"])
        )

    def test_rejects_wrong_elf_architecture(self) -> None:
        result = self._audit(inspector=self._inspector(machine="Advanced Micro Devices X86-64"))
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("unexpected ELF machine" in error for error in result["errors"]))

    def test_rejects_unsafe_runtime_rpath(self) -> None:
        result = self._audit(inspector=self._inspector(runtime_rpaths=["."]))
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("RPATH/RUNPATH" in error for error in result["errors"]))

    def test_allows_literal_origin_for_cpu_tools(self) -> None:
        result = self._audit(inspector=self._inspector(cpu_rpaths=["$ORIGIN"]))
        self.assertEqual(result["status"], "pass")

    def test_rejects_unsafe_cpu_tool_rpath(self) -> None:
        result = self._audit(inspector=self._inspector(cpu_rpaths=["."]))
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("cpu_test_runner contains RPATH/RUNPATH" in error for error in result["errors"]))

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
