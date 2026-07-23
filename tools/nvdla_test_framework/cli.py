from __future__ import annotations

import argparse
from pathlib import Path

from .abi import run_abi_check
from .board_artifact import run_board_artifact_import
from .diagnostics import classify_sdp_small_diagnostic
from .lenet import (
    DEFAULT_STOCK_DIR,
    analyze_lenet_artifact,
    build_lenet_small_workload,
    compare_lenet_control,
    fetch_lenet_sources,
)
from .lockcheck import run_lock_check
from .petalinux import run_petalinux_dts
from .petalinux_rootfs import run_petalinux_rootfs_audit
from .petalinux_sd import run_petalinux_sd_bundle
from .report import write_report
from .stock import run_stock_sdp_control
from .trace import DEFAULT_CSB_BASE, run_trace_compare, run_trace_parse
from .vp_audit import run_vp_small_config_audit
from .vp import run_vp_test
from .workloads import generate_workloads
from .xsa_audit import run_xsa_audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nvdla-test-framework")
    sub = parser.add_subparsers(dest="command", required=True)

    xsa = sub.add_parser("xsa-audit", help="Audit XSA hardware facts")
    xsa.add_argument("--xsa", required=True, type=Path)
    xsa.add_argument("--lock", required=True, type=Path)
    xsa.add_argument("--out", type=Path)

    lock = sub.add_parser("lock-check", help="Validate reproducibility lock")
    lock.add_argument("--lock", required=True, type=Path)
    lock.add_argument("--xsa", required=True, type=Path)
    lock.add_argument("--out", type=Path)

    vp = sub.add_parser("vp-test", help="Run a VP lane test")
    vp.add_argument("--lane", choices=["reference", "modern"], default="reference")
    vp.add_argument("--lock", required=True, type=Path)
    vp.add_argument("--timeout", type=int, default=35)
    vp.add_argument("--out-dir", type=Path)
    vp.add_argument("--work-dir", type=Path)
    vp.add_argument("--sources-dir", type=Path)
    vp.add_argument("--docker-image")
    vp.add_argument("--repeat", type=int, default=1)
    vp.add_argument("--mode", choices=["smoke", "runtime"], default="smoke")
    vp.add_argument("--workload", default="sdp_regression_small")

    workloads = sub.add_parser("workload-generate", help="Generate deterministic workload inputs/goldens")
    workloads.add_argument("--out", required=True, type=Path)

    abi = sub.add_parser("abi-check", help="Compare NVDLA KMD and UMD ioctl headers")
    abi.add_argument("--source", required=True, type=Path)
    abi.add_argument("--out", type=Path)

    report = sub.add_parser("report", help="Summarize artifact manifests")
    report.add_argument("--artifacts", required=True, type=Path)
    report.add_argument("--out", required=True, type=Path)

    lenet = sub.add_parser("lenet-compare", help="Compare stock and modern LeNet artifacts")
    lenet.add_argument("--stock-dir", type=Path, default=DEFAULT_STOCK_DIR)
    lenet.add_argument("--modern-dir", type=Path)
    lenet.add_argument("--out", type=Path)

    lenet_analyze = sub.add_parser("lenet-analyze", help="Analyze a LeNet correctness artifact")
    lenet_analyze.add_argument("--artifact", required=True, type=Path)
    lenet_analyze.add_argument("--expected-output")
    lenet_analyze.add_argument("--out", type=Path)

    lenet_sources = sub.add_parser("lenet-sources", help="Fetch pinned LeNet/MNIST source files")
    lenet_sources.add_argument("--lock", required=True, type=Path)
    lenet_sources.add_argument("--sources-dir", required=True, type=Path)

    lenet_workload = sub.add_parser("lenet-workload", help="Build pinned nv_small LeNet workload")
    lenet_workload.add_argument("--lock", required=True, type=Path)
    lenet_workload.add_argument("--sources-dir", required=True, type=Path)
    lenet_workload.add_argument("--out", required=True, type=Path)

    audit = sub.add_parser("vp-small-config-audit", help="Record nv_small VP/KMD configuration evidence")
    audit.add_argument("--lock", required=True, type=Path)
    audit.add_argument("--work-dir", required=True, type=Path)
    audit.add_argument("--artifacts", required=True, type=Path)

    sdp_diag = sub.add_parser("sdp-small-diagnostic", help="Classify the current nv_small SDP diagnostic run")
    sdp_diag.add_argument("--artifacts", required=True, type=Path)

    stock_sdp = sub.add_parser("stock-sdp-control", help="Run SDP through stock VP KMD/runtime")
    stock_sdp.add_argument("--lock", required=True, type=Path)
    stock_sdp.add_argument("--artifacts", required=True, type=Path)
    stock_sdp.add_argument("--workloads-dir", required=True, type=Path)
    stock_sdp.add_argument("--timeout", type=int, default=240)
    stock_sdp.add_argument("--host-port", type=int, default=6666)
    stock_sdp.add_argument("--workload", default="sdp_regression_full")

    pl_dts = sub.add_parser("petalinux-dts", help="Generate board-local NVDLA PetaLinux DTS fragment")
    pl_dts.add_argument("--lock", required=True, type=Path)
    pl_dts.add_argument("--xsa", required=True, type=Path)
    pl_dts.add_argument("--out", required=True, type=Path)
    pl_dts.add_argument("--audit-out", type=Path)

    pl_rootfs = sub.add_parser("petalinux-rootfs-audit", help="Audit NVDLA packages in a PetaLinux rootfs")
    pl_rootfs.add_argument("--rootfs", required=True, type=Path)
    pl_rootfs.add_argument("--extract-dir", required=True, type=Path)
    pl_rootfs.add_argument("--out", required=True, type=Path)

    pl_sd = sub.add_parser("petalinux-sd-bundle", help="Build a deterministic PetaLinux SD handoff bundle")
    pl_sd.add_argument("--boot-bin", required=True, type=Path)
    pl_sd.add_argument("--boot-script", required=True, type=Path)
    pl_sd.add_argument("--fit-image", required=True, type=Path)
    pl_sd.add_argument("--out-dir", required=True, type=Path)
    pl_sd.add_argument("--archive", required=True, type=Path)
    pl_sd.add_argument("--manifest", required=True, type=Path)

    board_import = sub.add_parser("board-artifact-import", help="Import a target-side NVDLA board evidence archive")
    board_import.add_argument("--archive", required=True, type=Path)
    board_import.add_argument("--out", required=True, type=Path)
    board_import.add_argument("--serial-log", type=Path)

    trace_parse = sub.add_parser("trace-parse", help="Canonicalize NVDLA VP SystemC transactions")
    trace_parse.add_argument("--input", required=True, type=Path)
    trace_parse.add_argument("--register-header", required=True, type=Path)
    trace_parse.add_argument("--csb-out", required=True, type=Path)
    trace_parse.add_argument("--raw-csb-out", required=True, type=Path)
    trace_parse.add_argument("--raw-dbb-out", required=True, type=Path)
    trace_parse.add_argument("--summary-out", required=True, type=Path)
    trace_parse.add_argument("--csb-base", type=lambda value: int(value, 0), default=DEFAULT_CSB_BASE)

    trace_compare = sub.add_parser("trace-compare", help="Compare canonical NVDLA CSB trace artifacts")
    trace_compare.add_argument("--reference-artifact", required=True, type=Path)
    trace_compare.add_argument("--candidate-artifact", required=True, type=Path)
    trace_compare.add_argument("--out", required=True, type=Path)

    args = parser.parse_args(argv)
    if args.command == "xsa-audit":
        return run_xsa_audit(args.xsa, args.lock, args.out)
    if args.command == "lock-check":
        return run_lock_check(args.lock, args.xsa, args.out)
    if args.command == "vp-test":
        return run_vp_test(
            args.lane,
            args.lock,
            args.timeout,
            args.out_dir,
            args.work_dir,
            args.sources_dir,
            args.docker_image,
            args.repeat,
            args.mode,
            args.workload,
        )
    if args.command == "workload-generate":
        return generate_workloads(args.out)
    if args.command == "abi-check":
        return run_abi_check(args.source, args.out)
    if args.command == "report":
        return write_report(args.artifacts, args.out)
    if args.command == "lenet-compare":
        return compare_lenet_control(args.stock_dir, args.modern_dir, args.out)
    if args.command == "lenet-analyze":
        return analyze_lenet_artifact(args.artifact, args.expected_output, args.out)
    if args.command == "lenet-sources":
        return fetch_lenet_sources(args.lock, args.sources_dir)
    if args.command == "lenet-workload":
        return build_lenet_small_workload(args.lock, args.sources_dir, args.out)
    if args.command == "vp-small-config-audit":
        return run_vp_small_config_audit(args.lock, args.work_dir, args.artifacts)
    if args.command == "sdp-small-diagnostic":
        return classify_sdp_small_diagnostic(args.artifacts)
    if args.command == "stock-sdp-control":
        return run_stock_sdp_control(
            args.lock,
            args.artifacts,
            args.workloads_dir,
            args.timeout,
            args.host_port,
            args.workload,
        )
    if args.command == "petalinux-dts":
        return run_petalinux_dts(args.lock, args.xsa, args.out, args.audit_out)
    if args.command == "petalinux-rootfs-audit":
        return run_petalinux_rootfs_audit(args.rootfs, args.extract_dir, args.out)
    if args.command == "petalinux-sd-bundle":
        return run_petalinux_sd_bundle(
            args.boot_bin,
            args.boot_script,
            args.fit_image,
            args.out_dir,
            args.archive,
            args.manifest,
        )
    if args.command == "board-artifact-import":
        return run_board_artifact_import(args.archive, args.out, args.serial_log)
    if args.command == "trace-parse":
        return run_trace_parse(
            args.input,
            args.register_header,
            args.csb_out,
            args.raw_csb_out,
            args.raw_dbb_out,
            args.summary_out,
            args.csb_base,
        )
    if args.command == "trace-compare":
        return run_trace_compare(args.reference_artifact, args.candidate_artifact, args.out)
    parser.error(f"unknown command {args.command}")
    return 2
