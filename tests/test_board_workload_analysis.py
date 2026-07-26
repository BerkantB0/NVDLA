from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nvdla_test_framework.board_artifact import (
    analyze_board_workload,
    parse_completed_operations,
    parse_hwl_progress,
    parse_hwl_progress,
    parse_interrupt_total,
    parse_unreturned_csb_read,
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

    def test_parses_generated_wrapper_interrupt_name(self) -> None:
        text = (
            " 59:          0          1          0          0  "
            "GICv2 121 Level a0000000.xilNvDlaWrapper\n"
        )
        self.assertEqual(parse_interrupt_total(text), 1)
        self.assertIsNone(parse_interrupt_total(text, "missing"))

    def test_parses_expected_lenet_operations(self) -> None:
        self.assertEqual(
            parse_completed_operations(_operation_log(EXPECTED_LENET_OPERATIONS)),
            EXPECTED_LENET_OPERATIONS,
        )

    def test_parses_last_resnet_hwl_progress(self) -> None:
        self.assertEqual(
            parse_hwl_progress(
                "10 HWLs done, totally 229 layers\n"
                "229 HWLs done, totally 229 layers\n"
            ),
            {"completed": 229, "total": 229},
        )

    def test_parses_last_resnet_hwl_progress(self) -> None:
        self.assertEqual(
            parse_hwl_progress(
                "10 HWLs done, totally 229 layers\n"
                "229 HWLs done, totally 229 layers\n"
            ),
            {"completed": 229, "total": 229},
        )

    def test_identifies_unreturned_csb_read(self) -> None:
        text = (
            "nvdla-trace csb-read begin offset=0x0000000c\n"
            "nvdla-trace csb-read end offset=0x0000000c value=0x000c0005\n"
            "nvdla-trace csb-read begin offset=0x00009004\n"
            "rcu: INFO: rcu_sched detected stalls on CPUs/tasks:\n"
        )
        self.assertEqual(
            parse_unreturned_csb_read(text),
            {
                "offset": "0x00009004",
                "physical_address": "0xa0009004",
                "register": "SDP_S_POINTER",
            },
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

    def test_identifies_sdp_prepare_csb_read_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "runtime-client.exit-status").write_text("124\n")
            (root / "runtime-server.exit-status").write_text("124\n")
            (root / "runtime-timeout.txt").write_text("runtime exceeded 10s\n")
            (root / "sdp-dmesg-delta.log").write_text(
                "Enter: dla_initiate_processors\n"
                "Prepare SDP operation index 0 ROI 0 dep_count 0\n"
                "Enter: dla_prepare_operation\n"
                "nvdla-trace csb-read begin offset=0x00009004\n"
            )
            (root / "sdp-irq-delta.txt").write_text("0\n")

            result = analyze_board_workload(
                root,
                {"mode": "runtime-sdp", "status": "1"},
                ["rcu: INFO: rcu_sched detected stalls on CPUs/tasks:"],
            )
            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["classification"], "kernel-log-failure")
            self.assertEqual(result["progress_classification"], "runtime-timeout")
            self.assertEqual(result["progress_stage"], "sdp-prepare-no-return")
            self.assertEqual(
                result["suspected_boundary"],
                "SDP_S_POINTER CSB read",
            )
            self.assertEqual(
                result["unreturned_csb_read"],
                {
                    "offset": "0x00009004",
                    "physical_address": "0xa0009004",
                    "register": "SDP_S_POINTER",
                },
            )

    def test_classifies_resnet_execution_with_pending_oracle(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repeat = root / "repeat-1"
            repeat.mkdir()
            (repeat / "result.env").write_text(
                "classification=execution-pass-oracle-pending\n"
                "expected_elements=1000\n"
            )
            (repeat / "runtime.exit-status").write_text("0\n")
            (repeat / "irq-delta.txt").write_text("229\n")
            (repeat / "dmesg-delta.log").write_text(
                "Exit: dla_initiate_processors status=0\n"
                "Completed Convolution operation index 0 ROI 0\n"
                "229 HWLs done, totally 229 layers\n"
            )
            output = " ".join(str(index % 127) for index in range(1000))
            (repeat / "output.txt").write_text(output + "\n")
            (repeat / "output.dimg").write_text(output + "\n")
            (repeat / "top5.txt").write_text("1 126 126\n2 253 126\n")

            result = analyze_board_workload(
                root,
                {"mode": "resnet50", "status": "0", "repeat_requested": "1"},
            )
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["classification"], "execution-pass-oracle-pending")
            self.assertEqual(result["correctness_status"], "inconclusive")
            self.assertEqual(result["repeat_results"][0]["output_elements"], 1000)
            self.assertTrue(result["repeat_results"][0]["task_initiated"])

    def test_recovers_resnet_top5_after_target_postprocessing_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repeat = root / "repeat-1"
            repeat.mkdir()
            (repeat / "runtime.exit-status").write_text("0\n")
            (repeat / "irq-delta.txt").write_text("246\n")
            (repeat / "dmesg-delta.log").write_text(
                "Exit: dla_initiate_processors status=0\n"
                "Completed SDP operation index 245 ROI 0\n"
                "246 HWLs done, totally 246 layers\n"
            )
            values = [0] * 1000
            values[278] = 50
            values[287] = 40
            output = " ".join(str(value) for value in values) + "\n"
            (repeat / "output.txt").write_text(output)
            (repeat / "output.dimg").write_text(output)

            result = analyze_board_workload(
                root,
                {"mode": "resnet50", "status": "1", "repeat_requested": "1"},
            )

            repeat_result = result["repeat_results"][0]
            self.assertEqual(repeat_result["status"], "pass")
            self.assertEqual(repeat_result["classification"], "execution-pass-oracle-pending")
            self.assertTrue(repeat_result["task_initiated"])
            self.assertEqual(
                repeat_result["top5"][:2],
                [
                    {"rank": 1, "index": 278, "value": 50},
                    {"rank": 2, "index": 287, "value": 40},
                ],
            )

    def test_rejects_partial_resnet_hwl_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repeat = root / "repeat-1"
            repeat.mkdir()
            (repeat / "result.env").write_text("expected_elements=1000\n")
            (repeat / "runtime.exit-status").write_text("0\n")
            (repeat / "irq-delta.txt").write_text("10\n")
            (repeat / "dmesg-delta.log").write_text(
                "Exit: dla_initiate_processors status=0\n"
                "Completed Convolution operation index 0 ROI 0\n"
                "20 HWLs done, totally 229 layers\n"
            )

            result = analyze_board_workload(
                root,
                {"mode": "resnet50", "status": "1", "repeat_requested": "1"},
            )
            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["classification"], "partial-hwl-sequence")
