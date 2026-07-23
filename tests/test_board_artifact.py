from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from nvdla_test_framework.board_artifact import import_board_artifact
from nvdla_test_framework.board_payload import EXPECTED_LENET_OPERATIONS, EXPECTED_LENET_OUTPUT


class BoardArtifactTests(unittest.TestCase):
    @staticmethod
    def _archive(path: Path, status: int = 0, bad_patterns: str = "") -> None:
        files = {
            "nvdla-board-smoke/result.env": (
                "schema_version=1\n"
                "mode=smoke\n"
                f"status={status}\n"
                "timestamp_utc=20260723T120000Z\n"
            ).encode("ascii"),
            "nvdla-board-smoke/bad-kernel-patterns.txt": bad_patterns.encode("utf-8"),
            "nvdla-board-smoke/dmesg-after.log": b"kernel log\n",
        }
        with tarfile.open(path, "w:gz") as archive:
            for name, data in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))

    def test_imports_passing_board_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "input.tar.gz"
            self._archive(archive)
            result = import_board_artifact(archive, root / "out")
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["mode"], "smoke")
            self.assertEqual(result["bad_kernel_patterns"], [])

    def test_preserves_failing_board_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "input.tar.gz"
            self._archive(archive, status=1)
            result = import_board_artifact(archive, root / "out")
            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["board_status"], 1)

    def test_rejects_traversal_member(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = root / "input.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                data = b"unsafe"
                member = tarfile.TarInfo("../escape")
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            with self.assertRaises(ValueError):
                import_board_artifact(archive_path, root / "out")

    def test_imports_and_analyzes_lenet_workload(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = root / "input.tar.gz"
            operation_log = "\n".join(
                f"Completed {item['processor']} operation index {item['index']} ROI 0"
                for item in EXPECTED_LENET_OPERATIONS
            )
            files = {
                "nvdla-board-lenet/result.env": (
                    "schema_version=2\n"
                    "mode=lenet\n"
                    "status=0\n"
                    "classification=exact-pass\n"
                    "repeat_requested=1\n"
                ).encode("ascii"),
                "nvdla-board-lenet/bad-kernel-patterns.txt": b"",
                "nvdla-board-lenet/repeat-1/result.env": (
                    "index=1\nstatus=0\nclassification=pass\n"
                ).encode("ascii"),
                "nvdla-board-lenet/repeat-1/runtime.exit-status": b"0\n",
                "nvdla-board-lenet/repeat-1/irq-delta.txt": b"10\n",
                "nvdla-board-lenet/repeat-1/dmesg-delta.log": (
                    "Exit: dla_initiate_processors status=0\n" + operation_log + "\n"
                ).encode("ascii"),
                "nvdla-board-lenet/repeat-1/output.txt": (
                    EXPECTED_LENET_OUTPUT + "\n"
                ).encode("ascii"),
                "nvdla-board-lenet/repeat-1/output.dimg": (
                    EXPECTED_LENET_OUTPUT + "\n"
                ).encode("ascii"),
            }
            with tarfile.open(archive_path, "w:gz") as archive:
                for name, data in files.items():
                    member = tarfile.TarInfo(name)
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))

            output = root / "out"
            result = import_board_artifact(archive_path, output)
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["classification"], "exact-pass")
            self.assertEqual(result["workload"]["pass_count"], 1)
            self.assertTrue((output / "workload-analysis.json").is_file())
