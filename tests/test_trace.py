from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nvdla_test_framework.trace import (
    canonicalize_vp_trace,
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


if __name__ == "__main__":
    unittest.main()
