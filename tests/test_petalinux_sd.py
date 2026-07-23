from __future__ import annotations

import tarfile
import tempfile
import unittest
from pathlib import Path

from nvdla_test_framework.petalinux_sd import build_petalinux_sd_bundle


class PetaLinuxSdBundleTests(unittest.TestCase):
    def _inputs(self, root: Path) -> tuple[Path, Path, Path]:
        boot_bin = root / "input" / "BOOT.BIN"
        boot_script = root / "input" / "boot.scr"
        fit_image = root / "input" / "image.ub"
        boot_bin.parent.mkdir(parents=True)
        boot_bin.write_bytes(b"boot-bin")
        boot_script.write_bytes(b"boot-script")
        fit_image.write_bytes(b"fit-image")
        return boot_bin, boot_script, fit_image

    def test_builds_complete_deterministic_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = self._inputs(root)
            first = build_petalinux_sd_bundle(
                *inputs,
                root / "first" / "sd-card",
                root / "first" / "bundle.tar.gz",
                root / "first" / "manifest.json",
            )
            second = build_petalinux_sd_bundle(
                *inputs,
                root / "second" / "sd-card",
                root / "second" / "bundle.tar.gz",
                root / "second" / "manifest.json",
            )

            self.assertEqual(first["status"], "pass")
            self.assertEqual(first["archive"]["sha256"], second["archive"]["sha256"])
            with tarfile.open(root / "first" / "bundle.tar.gz", "r:gz") as archive:
                self.assertEqual(
                    sorted(archive.getnames()),
                    ["BOOT.BIN", "SD-BUNDLE.json", "SHA256SUMS", "boot.scr", "image.ub"],
                )

    def test_rejects_missing_boot_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            boot_bin, boot_script, fit_image = self._inputs(root)
            boot_script.unlink()
            with self.assertRaises(FileNotFoundError):
                build_petalinux_sd_bundle(
                    boot_bin,
                    boot_script,
                    fit_image,
                    root / "sd-card",
                    root / "bundle.tar.gz",
                    root / "manifest.json",
                )
    def test_rejects_nonempty_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = self._inputs(root)
            out_dir = root / "sd-card"
            out_dir.mkdir()
            (out_dir / "stale.bin").write_bytes(b"stale")
            with self.assertRaises(ValueError):
                build_petalinux_sd_bundle(
                    *inputs,
                    out_dir,
                    root / "bundle.tar.gz",
                    root / "manifest.json",
                )
