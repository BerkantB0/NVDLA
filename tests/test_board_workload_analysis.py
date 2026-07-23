from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nvdla_test_framework.board_artifact import (
    analyze_board_workload,
    parse_completed_operations,
    parse_interrupt_total,
)
from nvdla_test_framework.board_payload import EXPECTED_LENET_OPERATIONS, EXPECTED_LENET_OUTPUT


def _operation_log(operations: list[dict[str, int | str]]) -> str:
    return "\n".join(
        f"Completed {item['processor']} operation index {item['index']} ROI 0"
        for item in operations
    )


class BoardWorkloadAnalysisTests(unittest.TestCase):
    def test_parses_multicore_nvdla_interrupt_total(self) -> None:
        text = (
            "           CPU0       CPU1       CPU2       CPU3\n"
            " 52:          1          2          3          4  GICv3 121 Level a0000000.nvdla\n"
        )
        self.assertEqual(parse_interrupt_total(text), 10)
        self.assertIsNone(parse_interrupt_total(text, "missing"))

    def test_parses_expected_lenet_operations(self) -> None:
        self.assertEqual(
            parse_completed_operations(_operation_log(EXPECTED_LENET_OPERATIONS)),
            EXPECTED_LENET_OPERATIONS,
        )

    def _lenet_root(
        self,
        operations: list[dict[str, int | str]] | None = None,
        irq_delta: int = 10,
        output: str = EXPECTED_LENET_OUTPUT,
        runtime_status: int = 0,
    ) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        repeat = root / "repeat-1"
        repeat.mkdir()
        log = "Exit: dla_initiate_processors status=0\n" + _operation_log(
            operations if operations is not None else EXPECTED_LENET_OPERATIONS
        )
        (repeat / "dmesg-delta.log").write_text(log)
        (repeat / "runtime.exit-status").write_text(f"{runtime_status}\n")
        (repeat / "irq-delta.txt").write_text(f"{irq_delta}\n")
        if output:
            (repeat / "output.txt").write_text(output + "\n")
            (repeat / "output.dimg").write_text(output + "\n")
        return temp, root

    def test_classifies_exact_lenet_pass(self) -> None:
        temp, root = self._lenet_root()
        with temp:
            result = analyze_board_workload(
                root,
                {"mode": "lenet", "status": "0", "repeat_requested": "1"},
            )
            self.assertIsNotNone(result)
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["classification"], "exact-pass")
            self.assertEqual(result["pass_count"], 1)

    def test_classifies_partial_sequence_and_next_engine(self) -> None:
        temp, root = self._lenet_root(EXPECTED_LENET_OPERATIONS[:3], irq_delta=3)
        with temp:
            result = analyze_board_workload(
                root,
                {"mode": "lenet", "status": "1", "repeat_requested": "1"},
            )
            self.assertEqual(result["classification"], "partial-operation-sequence")
            failure = result["first_failure"]
            self.assertEqual(failure["last_completed"], EXPECTED_LENET_OPERATIONS[2])
            self.assertEqual(failure["next_expected"], EXPECTED_LENET_OPERATIONS[3])

    def test_classifies_initiated_without_irq(self) -> None:
        temp, root = self._lenet_root([], irq_delta=0, output="")
        with temp:
            result = analyze_board_workload(
                root,
                {"mode": "lenet", "status": "1", "repeat_requested": "1"},
            )
            self.assertEqual(result["classification"], "initiated-without-irq")

    def test_classifies_output_mismatch_after_all_operations(self) -> None:
        temp, root = self._lenet_root(output="1 1 1 1 1 1 1 1 1 1")
        with temp:
            result = analyze_board_workload(
                root,
                {"mode": "lenet", "status": "1", "repeat_requested": "1"},
            )
            self.assertEqual(result["classification"], "output-mismatch")

    def test_records_first_missing_repeat(self) -> None:
        temp, root = self._lenet_root()
        with temp:
            result = analyze_board_workload(
                root,
                {"mode": "lenet", "status": "1", "repeat_requested": "2"},
            )
            self.assertEqual(result["pass_count"], 1)
            self.assertEqual(result["first_failure"]["index"], 2)
            self.assertEqual(result["classification"], "missing-repeat-evidence")

    def test_accepts_known_sdp_zero_payload_as_inconclusive(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "runtime-output" / "o_000000.dimg"
            output.parent.mkdir()
            output.write_bytes(b"header".ljust(40, b"\0") + bytes(64))
            (root / "runtime-client.exit-status").write_text("0\n")
            (root / "runtime-server.exit-status").write_text("0\n")
            (root / "runtime-client.stdout.log").write_text("[OK] Test PASSED!\n")
            (root / "sdp-dmesg-delta.log").write_text(
                "Exit: dla_initiate_processors status=0\n"
                "Handle op complete event, processor SDP group 0\n"
                "Completed SDP operation index 0 ROI 0\n"
            )
            (root / "sdp-irq-delta.txt").write_text("1\n")
            (root / "sdp-compare-status.txt").write_text("mismatch\n")
            (root / "sdp-payload-nonzero-bytes.txt").write_text("0\n")

            result = analyze_board_workload(
                root,
                {"mode": "runtime-sdp", "status": "0"},
            )
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["correctness_status"], "inconclusive")
            self.assertEqual(result["classification"], "diagnostic-pass-oracle-inconclusive")
