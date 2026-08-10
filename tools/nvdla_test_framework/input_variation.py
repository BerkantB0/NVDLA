from __future__ import annotations

import csv
import statistics
import tempfile
from pathlib import Path
from typing import Any

from .common import read_json, sha256_file, write_json
from .performance import _hash_records, _load_session_root, _parse_env, _read_profile


def _summary(values: list[int]) -> dict[str, float | int]:
    return {
        "count": len(values),
        "mean_ns": statistics.fmean(values),
        "median_ns": statistics.median(values),
        "stdev_ns": statistics.stdev(values) if len(values) > 1 else 0.0,
        "minimum_ns": min(values),
        "maximum_ns": max(values),
    }


def import_input_variation_archives(archives: list[Path], out_dir: Path) -> int:
    if not archives:
        raise ValueError("at least one input-variation archive is required")
    rows: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    boot_ids: set[str] = set()

    with tempfile.TemporaryDirectory() as temporary:
        extraction = Path(temporary)
        for session, archive in enumerate(archives, start=1):
            root = _load_session_root(archive, extraction / str(session))
            env = _parse_env(root / "benchmark.env")
            if (
                env.get("status") != "0"
                or env.get("classification")
                != "output-stable-input-variation-pass"
                or env.get("input_set") != "multi20"
            ):
                raise ValueError(f"{archive}: not a passing multi20 benchmark")
            if (root / "bad-kernel-patterns.txt").read_text().strip():
                raise ValueError(f"{archive}: kernel errors were recorded")
            if int((root / "irq-delta.txt").read_text().strip()) <= 0:
                raise ValueError(f"{archive}: NVDLA IRQ count did not increase")

            boot_id = env.get("boot_id", "")
            if not boot_id or boot_id in boot_ids:
                raise ValueError(f"{archive}: missing or reused Linux boot ID")
            boot_ids.add(boot_id)
            software = _hash_records(root / "software-hashes.txt")
            module = _hash_records(root / "module-hash.txt")
            uname = (root / "uname.txt").read_text().split()
            workload = read_json(root / "workload-manifest.json")
            set_manifest = root / "input-set-manifest.json"
            current = {
                "model": env.get("model"),
                "loadable_sha256": workload.get("loadable", {}).get("sha256"),
                "module_sha256": next(iter(module.values()), None),
                "runtime_sha256": software.get("nvdla_runtime"),
                "runtime_library_sha256": software.get("libnvdla_runtime.so"),
                "kernel_release": uname[2] if len(uname) >= 3 else None,
                "payload_sha256": software.get("SHA256SUMS"),
                "input_set_manifest_sha256": sha256_file(set_manifest),
                "nvdla_clock_hz": int(env.get("nvdla_clock_actual_hz", "0")),
                "benchmark_cpu": env.get("benchmark_cpu"),
            }
            if any(value in {None, "", 0} for value in current.values()):
                raise ValueError(f"{archive}: incomplete provenance")
            provenance.append(current)

            run = root / "steady-1"
            if (run / "verification.txt").read_text().strip() != "stable-output-pass":
                raise ValueError(f"{archive}: output qualification did not pass")
            with (run / "input-results.csv").open(newline="", encoding="ascii") as stream:
                results = {int(item["input_index"]): item for item in csv.DictReader(stream)}
            if set(results) != set(range(20)):
                raise ValueError(f"{archive}: incomplete input result table")

            profile = _read_profile(run / "profile.json")
            if int(profile.get("input_count", 0)) != 20:
                raise ValueError(f"{archive}: profile does not contain 20 inputs")
            measured = [sample for sample in profile["samples"] if not sample["warmup"]]
            counts = {index: 0 for index in range(20)}
            for sample in measured:
                input_index = int(sample["input_index"])
                counts[input_index] += 1
                result = results[input_index]
                rows.append(
                    {
                        "session": session,
                        "boot_id": boot_id,
                        "model": env["model"],
                        "sample_index": int(sample["index"]),
                        "input_index": input_index,
                        "expected_index": int(result["expected_index"]),
                        "predicted_index": int(result["predicted_index"]),
                        "acceptance": result["acceptance"],
                        "classification_match": int(result["classification_match"]),
                        "runtime_execution_ns": int(sample["runtime_execution_ns"]),
                        "input_update_ns": int(sample.get("input_update_ns", 0)),
                        "output_extract_ns": int(sample["output_extract_ns"]),
                    }
                )
            if len(set(counts.values())) != 1 or next(iter(counts.values())) == 0:
                raise ValueError(f"{archive}: measured samples are not balanced across inputs")

    baseline = provenance[0]
    if any(item != baseline for item in provenance[1:]):
        raise ValueError("refusing to combine input-variation sessions with different provenance")

    per_input = []
    for index in range(20):
        group = [row for row in rows if row["input_index"] == index]
        per_input.append(
            {
                "input_index": index,
                "expected_index": group[0]["expected_index"],
                "acceptance": group[0]["acceptance"],
                "runtime_execution": _summary(
                    [row["runtime_execution_ns"] for row in group]
                ),
                "input_update": _summary([row["input_update_ns"] for row in group]),
            }
        )
    medians = [item["runtime_execution"]["median_ns"] for item in per_input]
    overall = _summary([row["runtime_execution_ns"] for row in rows])
    median_range = max(medians) - min(medians)
    summary = {
        "schema_version": 1,
        "status": "pass",
        "sessions": len(archives),
        "provenance": baseline,
        "runtime_execution": overall,
        "input_update": _summary([row["input_update_ns"] for row in rows]),
        "classification": {
            "matches": sum(row["classification_match"] for row in rows),
            "total": len(rows),
            "accuracy_percent": 100.0
            * sum(row["classification_match"] for row in rows)
            / len(rows),
            "meaning": "top-1 for LeNet and top-5 for ResNet-50",
        },
        "per_input": per_input,
        "between_input_median_range_ns": median_range,
        "between_input_median_range_percent": median_range * 100.0 / overall["median_ns"],
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "input-variation-raw.csv").open("w", newline="", encoding="ascii") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    write_json(out_dir / "input-variation-summary.json", summary)
    lines = [
        f"# {baseline['model']} Multi-Input Sensitivity",
        "",
        f"- Sessions: {len(archives)} fresh boots",
        "- Inputs: 20, balanced within every session",
        f"- Measured executions: {len(rows)}",
        f"- Overall median loaded-context execution: {overall['median_ns'] / 1e6:.3f} ms",
        f"- Range across per-input medians: {median_range / 1e6:.3f} ms "
        f"({summary['between_input_median_range_percent']:.2f}% of the overall median)",
        f"- Median prepared-input buffer update: {summary['input_update']['median_ns'] / 1e6:.3f} ms",
        f"- Recorded classification accuracy: {summary['classification']['accuracy_percent']:.1f}% "
        f"({summary['classification']['matches']}/{summary['classification']['total']})",
        "",
        "Every input produced an output that remained stable when repeated, IRQ activity "
        "increased, and no bad kernel pattern was recorded. Classification accuracy is "
        "reported separately and is not treated as a hardware execution criterion.",
    ]
    (out_dir / "input-variation-report.md").write_text(
        "\n".join(lines) + "\n", encoding="ascii"
    )
    print(f"Input-variation report: {out_dir / 'input-variation-report.md'}")
    return 0
