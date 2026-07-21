from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nvdla_test_framework.trace import (
    canonicalize_vp_trace,
    compare_normalized_traces,
    normalize_csb_events,
    parse_register_map,
    parse_vp_transactions,
    split_raw_transactions,
    summarize_trace,
)


CSB_WRITE = (
    "Info: nvdla.csb_adaptor: GP: iswrite=1 addr=0x3010 len=4 "
    "data=0x 00000001 resp=TLM_OK_RESPONSE"
)
CSB_READ = (
    "Info: nvdla.csb_adaptor: GP: iswrite=0 addr=0x1020100c len=4 "
    "data=0x 00100000 resp=TLM_OK_RESPONSE"
)
DBB_WRITE = (
    "Info: nvdla.dbb_adaptor: GP: iswrite=1 addr=0xc0001e80 len=16 "
    "data=0x abcd01b0 abcd01b1 abcd01b2 abcd01b3 resp=TLM_OK_RESPONSE"
)


def event(
    sequence: int,
    operation: str,
    offset: int,
    data: int,
    register: str,
    source: str = "vp",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "sequence": sequence,
        "source": source,
        "interface": "csb",
        "operation": operation,
        "offset": f"0x{offset:08x}",
        "length": 4,
        "data": f"0x{data:08x}",
        "response": "ok",
        "register": register,
    }


class TraceParserTests(unittest.TestCase):
    def test_parses_csb_relative_and_absolute_addresses(self) -> None:
        registers = {0x3010: "CDMA_D_OP_ENABLE_0", 0x100C: "GLB_S_INTR_STATUS_0"}
        events = parse_vp_transactions([CSB_WRITE, CSB_READ], registers)

        self.assertEqual(events[0]["operation"], "write")
        self.assertEqual(events[0]["offset"], "0x00003010")
        self.assertEqual(events[0]["data"], "0x00000001")
        self.assertEqual(events[0]["register"], "CDMA_D_OP_ENABLE_0")
        self.assertEqual(events[1]["operation"], "read")
        self.assertEqual(events[1]["offset"], "0x0000100c")
        self.assertEqual(events[1]["register"], "GLB_S_INTR_STATUS_0")

    def test_parses_multiword_dbb_transaction(self) -> None:
        event = parse_vp_transactions([DBB_WRITE])[0]
        self.assertEqual(event["interface"], "dbb")
        self.assertEqual(event["length"], 16)
        self.assertEqual(event["data"], "0xabcd01b0abcd01b1abcd01b2abcd01b3")
        self.assertIsNone(event["register"])

    def test_rejects_malformed_transaction(self) -> None:
        with self.assertRaisesRegex(ValueError, "malformed VP transaction"):
            parse_vp_transactions(["Info: nvdla.csb_adaptor: GP: broken"])

    def test_records_non_ok_response(self) -> None:
        line = CSB_READ.replace("TLM_OK_RESPONSE", "TLM_ADDRESS_ERROR_RESPONSE")
        summary = summarize_trace(parse_vp_transactions([line]))
        self.assertEqual(summary["non_ok_responses"], 1)
        self.assertEqual(summary["responses"], {"address_error": 1})

    def test_splits_raw_interfaces(self) -> None:
        csb, dbb = split_raw_transactions(["ignored", CSB_WRITE, DBB_WRITE])
        self.assertEqual(csb, [CSB_WRITE])
        self.assertEqual(dbb, [DBB_WRITE])

    def test_extracts_register_names_from_generated_header(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            header = Path(td) / "opendla_small.h"
            header.write_text(
                "#define CDMA_D_OP_ENABLE_0 _MK_ADDR_CONST(0x3010)\n"
                "#define CDMA_D_OP_ENABLE_0_OP_EN_SHIFT _MK_SHIFT_CONST(0)\n",
                encoding="utf-8",
            )
            self.assertEqual(parse_register_map(header), {0x3010: "CDMA_D_OP_ENABLE_0"})

    def test_streams_canonical_csb_and_raw_interface_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "systemc.log"
            header = root / "opendla_small.h"
            source.write_text(f"ignored\n{CSB_WRITE}\n{DBB_WRITE}\n", encoding="utf-8")
            header.write_text(
                "#define CDMA_D_OP_ENABLE_0 _MK_ADDR_CONST(0x3010)\n",
                encoding="utf-8",
            )

            summary = canonicalize_vp_trace(
                source,
                header,
                root / "csb-events.jsonl",
                root / "csb.raw.log",
                root / "dbb.raw.log",
                root / "trace-summary.json",
            )

            self.assertEqual(summary["csb_event_count"], 1)
            self.assertEqual(summary["dbb_event_count"], 1)
            self.assertIn("CDMA_D_OP_ENABLE_0", (root / "csb-events.jsonl").read_text())
            self.assertEqual((root / "csb.raw.log").read_text().strip(), CSB_WRITE)
            self.assertEqual((root / "dbb.raw.log").read_text().strip(), DBB_WRITE)

    def test_equivalent_polling_and_dma_addresses_compare_equal(self) -> None:
        reference = [
            event(0, "read", 0x100C, 0, "GLB_S_INTR_STATUS_0"),
            event(1, "read", 0x100C, 0, "GLB_S_INTR_STATUS_0"),
            event(2, "write", 0x3038, 0xC0016000, "CDMA_D_DAIN_ADDR_LOW_0_0"),
            event(3, "write", 0x3010, 1, "CDMA_D_OP_ENABLE_0"),
        ]
        candidate = [
            event(0, "read", 0x100C, 0, "GLB_S_INTR_STATUS_0", source="ila"),
            event(1, "write", 0x3038, 0xC1016000, "CDMA_D_DAIN_ADDR_LOW_0_0", source="ila"),
            event(2, "write", 0x3010, 1, "CDMA_D_OP_ENABLE_0", source="ila"),
        ]
        normalized_reference, reference_policy = normalize_csb_events(reference)
        normalized_candidate, candidate_policy = normalize_csb_events(candidate)

        self.assertFalse(reference_policy["errors"])
        self.assertFalse(candidate_policy["errors"])
        self.assertTrue(compare_normalized_traces(normalized_reference, normalized_candidate)["match"])

    def test_changed_write_value_and_order_fail(self) -> None:
        reference, _ = normalize_csb_events(
            [
                event(0, "write", 0x3004, 2, "CDMA_D_MISC_CFG_0"),
                event(1, "write", 0x3010, 1, "CDMA_D_OP_ENABLE_0"),
            ]
        )
        changed_value, _ = normalize_csb_events(
            [
                event(0, "write", 0x3004, 3, "CDMA_D_MISC_CFG_0"),
                event(1, "write", 0x3010, 1, "CDMA_D_OP_ENABLE_0"),
            ]
        )
        reordered, _ = normalize_csb_events(list(reversed([
            event(0, "write", 0x3004, 2, "CDMA_D_MISC_CFG_0"),
            event(1, "write", 0x3010, 1, "CDMA_D_OP_ENABLE_0"),
        ])))

        self.assertFalse(compare_normalized_traces(reference, changed_value)["match"])
        self.assertGreater(compare_normalized_traces(reference, changed_value)["counts"]["value_mismatched"], 0)
        self.assertFalse(compare_normalized_traces(reference, reordered)["match"])
        self.assertGreater(compare_normalized_traces(reference, reordered)["counts"]["reordered"], 0)

    def test_missing_enable_and_changed_offset_fail(self) -> None:
        reference, _ = normalize_csb_events(
            [
                event(0, "write", 0x3004, 2, "CDMA_D_MISC_CFG_0"),
                event(1, "write", 0x3010, 1, "CDMA_D_OP_ENABLE_0"),
            ]
        )
        missing, _ = normalize_csb_events([event(0, "write", 0x3004, 2, "CDMA_D_MISC_CFG_0")])
        changed_offset, _ = normalize_csb_events(
            [
                event(0, "write", 0x3008, 2, "CDMA_D_DATAIN_FORMAT_0"),
                event(1, "write", 0x3010, 1, "CDMA_D_OP_ENABLE_0"),
            ]
        )

        self.assertEqual(compare_normalized_traces(reference, missing)["counts"]["missing"], 1)
        self.assertFalse(compare_normalized_traces(reference, changed_offset)["match"])

    def test_rejects_invalid_dma_address_and_non_ok_response(self) -> None:
        invalid = event(0, "write", 0x3038, 0x80000001, "CDMA_D_DAIN_ADDR_LOW_0_0")
        failed = event(1, "read", 0x100C, 0, "GLB_S_INTR_STATUS_0")
        failed["response"] = "address_error"
        _, policy = normalize_csb_events([invalid, failed])
        self.assertEqual(len(policy["errors"]), 2)

    def test_interrupt_service_may_interleave_without_weakening_each_stream(self) -> None:
        pointer = event(0, "write", 0xB004, 0, "PDP_S_POINTER_0")
        interrupt_read = event(1, "read", 0x100C, 0x15, "GLB_S_INTR_STATUS_0")
        interrupt_clear = event(2, "write", 0x100C, 0x15, "GLB_S_INTR_STATUS_0")
        enable = event(3, "write", 0xB010, 1, "PDP_D_OP_ENABLE_0")
        reference, _ = normalize_csb_events([pointer, interrupt_read, interrupt_clear, enable])
        candidate, _ = normalize_csb_events([interrupt_read, interrupt_clear, pointer, enable])
        changed_interrupt, _ = normalize_csb_events(
            [event(0, "read", 0x100C, 0x16, "GLB_S_INTR_STATUS_0"), interrupt_clear, pointer, enable]
        )

        self.assertTrue(compare_normalized_traces(reference, candidate)["match"])
        self.assertFalse(compare_normalized_traces(reference, changed_interrupt)["match"])


if __name__ == "__main__":
    unittest.main()
