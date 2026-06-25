from __future__ import annotations

import argparse
from pathlib import Path

from .abi import run_abi_check
from .lockcheck import run_lock_check
from .report import write_report
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

    workloads = sub.add_parser("workload-generate", help="Generate deterministic workload inputs/goldens")
    workloads.add_argument("--out", required=True, type=Path)

    abi = sub.add_parser("abi-check", help="Compare NVDLA KMD and UMD ioctl headers")
    abi.add_argument("--source", required=True, type=Path)
    abi.add_argument("--out", type=Path)

    report = sub.add_parser("report", help="Summarize artifact manifests")
    report.add_argument("--artifacts", required=True, type=Path)
    report.add_argument("--out", required=True, type=Path)

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
        )
    if args.command == "workload-generate":
        return generate_workloads(args.out)
    if args.command == "abi-check":
        return run_abi_check(args.source, args.out)
    if args.command == "report":
        return write_report(args.artifacts, args.out)
    parser.error(f"unknown command {args.command}")
    return 2
