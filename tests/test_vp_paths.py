from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nvdla_test_framework.vp import (
    _bad_patterns,
    _extract_probe_config,
    _modern_paths,
    _workload_config_check,
    _write_modern_lua,
)


class ModernVpPathTests(unittest.TestCase):
    def test_prefers_smoke_artifacts_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work = root / "work"
            sources = root / "sources"
            kernel_dir = work / "kernel" / "arch" / "arm64" / "boot"
            images_dir = work / "buildroot" / "images"
            modules_dir = work / "modules"
            for path in (kernel_dir, images_dir, modules_dir):
                path.mkdir(parents=True)

            image = kernel_dir / "Image"
            image_vp2m = kernel_dir / "Image.vp2m"
            rootfs = images_dir / "rootfs.ext4"
            rootfs_smoke = images_dir / "rootfs-smoke.ext4"
            module = modules_dir / "opendla.ko"
            for path in (image, image_vp2m, rootfs, rootfs_smoke, module):
                path.write_bytes(b"x")

            paths = _modern_paths(work, sources)

            self.assertEqual(paths["kernel"], image_vp2m)
            self.assertEqual(paths["rootfs"], rootfs_smoke)
            self.assertEqual(paths["module"], module)
            self.assertEqual(paths["runtime_binary"], work / "runtime" / "nvdla_runtime")
            self.assertEqual(paths["runtime_library"], work / "runtime" / "libnvdla_runtime.so")

    def test_explicit_artifact_overrides_win(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work = root / "work"
            sources = root / "sources"
            kernel = root / "custom-Image"
            rootfs = root / "custom-rootfs.ext4"
            module = root / "custom-opendla.ko"
            runtime_binary = root / "custom-runtime"
            runtime_library = root / "custom-runtime.so"
            workloads_dir = root / "custom-workloads"
            for path in (kernel, rootfs, module, runtime_binary, runtime_library):
                path.write_bytes(b"x")
            workloads_dir.mkdir()

            env = {
                "VP_MODERN_KERNEL": os.fspath(kernel),
                "VP_MODERN_ROOTFS": os.fspath(rootfs),
                "VP_MODERN_KO": os.fspath(module),
                "VP_RUNTIME_BIN": os.fspath(runtime_binary),
                "VP_RUNTIME_LIB": os.fspath(runtime_library),
                "WORKLOADS_DIR": os.fspath(workloads_dir),
            }
            with patch.dict(os.environ, env, clear=False):
                paths = _modern_paths(work, sources)

            self.assertEqual(paths["kernel"], kernel)
            self.assertEqual(paths["rootfs"], rootfs)
            self.assertEqual(paths["module"], module)
            self.assertEqual(paths["runtime_binary"], runtime_binary)
            self.assertEqual(paths["runtime_library"], runtime_library)
            self.assertEqual(paths["workloads_dir"], workloads_dir)

    def test_modern_lua_matches_qemu_virt_ram_base(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            kernel = root / "Image.vp2m"
            rootfs = root / "rootfs-smoke.ext4"
            kernel.write_bytes(b"kernel")
            rootfs.write_bytes(b"rootfs")

            lua = _write_modern_lua({"kernel": kernel, "rootfs": rootfs, "dtb": None}, root)
            text = lua.read_text(encoding="utf-8")

            self.assertIn("base_addr = 0x40000000", text)
            self.assertIn("high_addr = 0x7fffffff", text)
            self.assertIn("-kernel /vp-kernel/Image.vp2m", text)

    def test_small_lua_uses_source_docker_paths_and_extmem_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            kernel = root / "Image.vp2m"
            rootfs = root / "rootfs-smoke.ext4"
            dtb = root / "small.dtb"
            kernel.write_bytes(b"kernel")
            rootfs.write_bytes(b"rootfs")
            dtb.write_bytes(b"dtb")

            with patch.dict(os.environ, {"VP_HW_CONFIG": "small"}, clear=False):
                lua = _write_modern_lua({"kernel": kernel, "rootfs": rootfs, "dtb": dtb}, root)
            text = lua.read_text(encoding="utf-8")

            self.assertIn("base_addr = 0xc0000000", text)
            self.assertIn("high_addr = 0xffffffff", text)
            self.assertIn("-kernel", text)
            self.assertIn("Image.vp2m", text)
            self.assertIn("-dtb", text)
            self.assertIn("small.dtb", text)
            self.assertIn("/vp-kernel/Image.vp2m", text)
            self.assertIn("/vp-rootfs/rootfs-smoke.ext4", text)
            self.assertIn("/vp-dtb/small.dtb", text)
            self.assertIn("/payload", text)

    def test_small_lua_can_use_host_paths_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            kernel = root / "Image.vp2m"
            rootfs = root / "rootfs-smoke.ext4"
            dtb = root / "small.dtb"
            kernel.write_bytes(b"kernel")
            rootfs.write_bytes(b"rootfs")
            dtb.write_bytes(b"dtb")

            with patch.dict(os.environ, {"VP_HW_CONFIG": "small", "VP_RUNNER": "host"}, clear=False):
                lua = _write_modern_lua({"kernel": kernel, "rootfs": rootfs, "dtb": dtb}, root)
            text = lua.read_text(encoding="utf-8")

            def lua_path(path: Path) -> str:
                return str(path).replace("\\", "\\\\")

            self.assertIn("base_addr = 0xc0000000", text)
            self.assertIn("high_addr = 0xffffffff", text)
            self.assertIn(lua_path(kernel), text)
            self.assertIn(lua_path(rootfs), text)
            self.assertIn(lua_path(dtb), text)
            self.assertNotIn("/vp-kernel", text)

    def test_small_paths_prefer_small_extmem_dtb_and_vp_binary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work = root / "work"
            sources = root / "sources"
            dtb = work / "dtb" / "nvdla-vp-modern-small-extmem-pool.dtb"
            vp_binary = work / "vp-small" / "install" / "bin" / "aarch64_toplevel"
            dtb.parent.mkdir(parents=True)
            vp_binary.parent.mkdir(parents=True)
            dtb.write_bytes(b"dtb")
            vp_binary.write_bytes(b"vp")

            with patch.dict(os.environ, {"VP_HW_CONFIG": "small"}, clear=False):
                paths = _modern_paths(work, sources)

            self.assertEqual(paths["dtb"], dtb)
            self.assertEqual(paths["vp_binary"], vp_binary)
            self.assertEqual(paths["nvdla_vp"], sources / "nvdla-vp")
            self.assertEqual(paths["nvdla_hw"], sources / "nvdla-hw")

    def test_bad_patterns_cover_vp_and_systemc_failures(self) -> None:
        log = """
        GP: TLM_ADDRESS_ERROR_RESPONSE
        Error: (E115) sc_signal<T> cannot have more than one driver
        """

        bad = _bad_patterns(log)

        self.assertIn("TLM_ADDRESS_ERROR_RESPONSE", bad)
        self.assertIn("sc_signal<.*cannot have more than one driver", bad)
        self.assertIn("Error: \\(E[0-9]+\\)", bad)

    def test_probe_config_is_extracted_from_driver_log(self) -> None:
        log = "opendla: loading\nProbe NVDLA config nvidia,nvdla_os_initial\n"

        self.assertEqual(_extract_probe_config(log), "nvidia,nvdla_os_initial")

    def test_workload_config_check_reports_mismatch(self) -> None:
        manifest = {
            "target": {
                "config": "nv_small",
                "compatible": ["nvidia,nv_small"],
            }
        }

        result = _workload_config_check(manifest, "nvidia,nvdla_os_initial")

        self.assertEqual(result["status"], "fail")
        self.assertIn("workload expects", result["reason"])

    def test_workload_config_check_accepts_aliases(self) -> None:
        manifest = {
            "target": {
                "config": "nv_full",
                "compatible": ["nvidia,nvdla_os_initial", "nvidia,nv_full"],
            }
        }

        result = _workload_config_check(manifest, "nvidia,nvdla_os_initial")

        self.assertEqual(result["status"], "pass")


if __name__ == "__main__":
    unittest.main()
