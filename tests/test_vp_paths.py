from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nvdla_test_framework.vp import _modern_paths


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

    def test_explicit_artifact_overrides_win(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work = root / "work"
            sources = root / "sources"
            kernel = root / "custom-Image"
            rootfs = root / "custom-rootfs.ext4"
            module = root / "custom-opendla.ko"
            for path in (kernel, rootfs, module):
                path.write_bytes(b"x")

            env = {
                "VP_MODERN_KERNEL": os.fspath(kernel),
                "VP_MODERN_ROOTFS": os.fspath(rootfs),
                "VP_MODERN_KO": os.fspath(module),
            }
            with patch.dict(os.environ, env, clear=False):
                paths = _modern_paths(work, sources)

            self.assertEqual(paths["kernel"], kernel)
            self.assertEqual(paths["rootfs"], rootfs)
            self.assertEqual(paths["module"], module)


if __name__ == "__main__":
    unittest.main()
