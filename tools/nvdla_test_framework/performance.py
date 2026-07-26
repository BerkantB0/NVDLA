from __future__ import annotations

import csv
import json
import math
import random
import statistics
import sys
import tarfile
import tempfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .common import read_json, sha256_file, write_json


PHASES = (
    "runtime_create",
    "loadable_read",
    "runtime_load",
    "emu_init",
    "input_setup",
    "output_setup",
    "output_write",
    "buffer_cleanup",
    "emu_stop",
    "runtime_unload",
    "runtime_destroy",
)
ENGINES = ("Convolution", "SDP", "PDP", "CDP", "Rubik", "BDMA")
PROVENANCE_KEYS = (
    "model",
    "input_sha256",
    "loadable_sha256",
    "module_sha256",
    "runtime_sha256",
    "runtime_library_sha256",
    "kernel_release",
    "nvdla_clock_hz",
    "nvdla_clock_expected_hz",
    "nvdla_clock_tolerance_hz",
    "payload_sha256",
    "firmware_log",
    "benchmark_cpu",
)


def _parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def _safe_extract(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:*") as bundle:
        for member in bundle.getmembers():
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise ValueError(f"unsafe archive member: {member.name}")
            if member.issym() or member.islnk():
                raise ValueError(f"links are not accepted in benchmark archives: {member.name}")
        if sys.version_info >= (3, 12):
            bundle.extractall(destination, filter="data")
        else:
            bundle.extractall(destination)


def _hash_records(path: Path) -> dict[str, str]:
    records: dict[str, str] = {}
    if not path.is_file():
        return records
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = line.split(maxsplit=1)
        if len(fields) == 2:
            records[Path(fields[1].lstrip("*")).name] = fields[0].lower()
    return records


def _load_session_root(archive: Path, extraction: Path) -> Path:
    _safe_extract(archive, extraction)
    candidates = list(extraction.rglob("benchmark.env"))
    if len(candidates) != 1:
        raise ValueError(
            f"{archive} must contain exactly one benchmark.env; found {len(candidates)}"
        )
    return candidates[0].parent


def _provenance(root: Path, env: dict[str, str]) -> dict[str, Any]:
    workload = read_json(root / "workload-manifest.json")
    software = _hash_records(root / "software-hashes.txt")
    module = _hash_records(root / "module-hash.txt")
    uname = (root / "uname.txt").read_text(encoding="utf-8", errors="replace").split()
    kernel_release = uname[2] if len(uname) >= 3 else None
    image = workload.get("image", {})
    loadable = workload.get("loadable", {})
    complexity = workload.get("complexity", {})
    operation_counts = complexity.get("operation_counts", {})
    if (
        not isinstance(operation_counts, dict)
        or set(operation_counts) != set(ENGINES)
        or sum(operation_counts.values()) != complexity.get("hwl_count")
        or complexity.get("loadable_size_bytes") != loadable.get("size_bytes")
    ):
        raise ValueError("invalid workload complexity evidence")
    payload_hash = software.get("SHA256SUMS")
    if env.get("nvdla_clock_status") != "verified-xsa-rate":
        raise ValueError("benchmark did not verify the XSA-derived NVDLA clock")
    rate = int(env.get("nvdla_clock_actual_hz", "0"))
    expected_rate = int(env.get("nvdla_clock_expected_hz", "0"))
    tolerance = int(env.get("nvdla_clock_tolerance_hz", "-1"))
    if (
        rate <= 0
        or expected_rate <= 0
        or tolerance < 0
        or abs(rate - expected_rate) > tolerance
    ):
        raise ValueError("invalid NVDLA clock evidence")
    result = {
        "model": env.get("model"),
        "input_sha256": str(image.get("sha256", "")).lower() or None,
        "loadable_sha256": str(loadable.get("sha256", "")).lower() or None,
        "module_sha256": next(iter(module.values()), None),
        "runtime_sha256": software.get("nvdla_runtime"),
        "runtime_library_sha256": software.get("libnvdla_runtime.so"),
        "kernel_release": kernel_release,
        "nvdla_clock_hz": rate,
        "nvdla_clock_expected_hz": expected_rate,
        "nvdla_clock_tolerance_hz": tolerance,
        "payload_sha256": payload_hash,
        "firmware_log": env.get("firmware_log"),
        "benchmark_cpu": env.get("benchmark_cpu"),
        "workload_complexity": complexity,
    }
    missing = [key for key in PROVENANCE_KEYS if result.get(key) in {None, ""}]
    if missing:
        raise ValueError(f"incomplete benchmark provenance: {', '.join(missing)}")
    return result


def _read_profile(path: Path) -> dict[str, Any]:
    profile = read_json(path)
    if int(profile.get("schema_version", 0)) != 2:
        raise ValueError(f"{path}: unsupported performance profile schema")
    if profile.get("clock") != "CLOCK_MONOTONIC_RAW":
        raise ValueError(f"{path}: unsupported clock")
    if int(profile.get("clock_resolution_ns", 0)) <= 0:
        raise ValueError(f"{path}: invalid clock resolution")
    if int(profile.get("clock_pair_overhead_ns", -1)) < 0:
        raise ValueError(f"{path}: missing clock-pair overhead")
    if profile.get("outputs_consistent") is not True:
        raise ValueError(f"{path}: repeated outputs were not identical")
    if int(profile.get("status", -1)) != 0:
        raise ValueError(f"{path}: runtime status was not successful")
    return profile


def _profile_rows(
    root: Path,
    session: str,
    model: str,
    benchmark_cpu: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for regime in ("cold", "warm", "steady"):
        for run_dir in sorted(root.glob(f"{regime}-*")):
            if not run_dir.is_dir():
                continue
            if (run_dir / "verification.txt").read_text().strip() != "exact-pass":
                raise ValueError(f"{run_dir}: output verification did not pass")
            run_env = _parse_env(run_dir / "run.env")
            if int(run_env.get("irq_delta", "0")) <= 0:
                raise ValueError(f"{run_dir}: NVDLA IRQ count did not increase")
            profile_path = run_dir / "profile.json"
            profile = _read_profile(profile_path)
            rusage = _parse_env(run_dir / "rusage.env")
            if rusage.get("cpu_affinity") != benchmark_cpu:
                raise ValueError(f"{run_dir}: runtime CPU affinity does not match session")
            scheduling_fields = (
                "user_time_ns",
                "system_time_ns",
                "minor_page_faults",
                "major_page_faults",
                "voluntary_context_switches",
                "involuntary_context_switches",
            )
            missing_scheduling = [
                field for field in scheduling_fields if field not in rusage
            ]
            if missing_scheduling:
                raise ValueError(
                    f"{run_dir}: incomplete process scheduling evidence: "
                    + ", ".join(missing_scheduling)
                )
            phases = profile["phases_ns"]
            clock_overhead_ns = int(profile["clock_pair_overhead_ns"])
            launch_ns = (
                int((run_dir / "launch-elapsed-ns.txt").read_text().strip())
                if (run_dir / "launch-elapsed-ns.txt").is_file()
                else None
            )
            for sample in profile["samples"]:
                if sample.get("warmup"):
                    continue
                runtime_execution_ns = int(sample["runtime_execution_ns"])
                if runtime_execution_ns <= 0:
                    raise ValueError(
                        f"{profile_path}: invalid runtime execution latency"
                    )
                overhead_fraction = clock_overhead_ns / runtime_execution_ns
                if overhead_fraction > 0.01:
                    raise ValueError(
                        f"{profile_path}: clock measurement overhead exceeds 1% "
                        f"of runtime execution latency"
                    )
                latency_ns = (
                    launch_ns
                    if regime in {"cold", "warm"}
                    else runtime_execution_ns
                )
                row: dict[str, Any] = {
                    "session": session,
                    "model": model,
                    "regime": regime,
                    "run": run_dir.name,
                    "sample_index": int(sample["index"]),
                    "latency_ns": latency_ns,
                    "runtime_execution_ns": runtime_execution_ns,
                    "output_extract_ns": int(sample["output_extract_ns"]),
                    "launch_elapsed_ns": launch_ns,
                    "process_total_ns": int(phases["process_total"]),
                    "clock_resolution_ns": int(profile["clock_resolution_ns"]),
                    "clock_pair_overhead_ns": clock_overhead_ns,
                    "clock_overhead_fraction": overhead_fraction,
                    "profile_path": str(profile_path.relative_to(root)),
                    "profile_total_iterations": len(profile["samples"]),
                    "profile_measured_iterations": int(
                        profile["measured_iterations"]
                    ),
                    "profile_warmup_iterations": int(profile["warmup_iterations"]),
                    "cpu_affinity": rusage["cpu_affinity"],
                    "process_user_time_ns": int(rusage["user_time_ns"]),
                    "process_system_time_ns": int(rusage["system_time_ns"]),
                    "minor_page_faults": int(rusage["minor_page_faults"]),
                    "major_page_faults": int(rusage["major_page_faults"]),
                    "voluntary_context_switches": int(
                        rusage["voluntary_context_switches"]
                    ),
                    "involuntary_context_switches": int(
                        rusage["involuntary_context_switches"]
                    ),
                    "cpu_migrations": rusage.get("cpu_migrations", "unavailable"),
                }
                for phase in PHASES:
                    row[f"phase_{phase}_ns"] = int(phases.get(phase, 0))
                row["outside_runtime_execution_ns"] = max(
                    0, int(latency_ns) - runtime_execution_ns
                )
                rows.append(row)
    if not rows:
        raise ValueError(f"{root}: no cold, warm, or steady measurements found")
    return rows


def percentile(values: Iterable[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires at least one value")
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return float(ordered[lower] * (1 - fraction) + ordered[upper] * fraction)


def summarize_values(values: Iterable[float]) -> dict[str, Any]:
    data = [float(value) for value in values]
    if not data:
        raise ValueError("summary requires at least one value")
    mean = statistics.fmean(data)
    q1 = percentile(data, 0.25)
    q3 = percentile(data, 0.75)
    iqr = q3 - q1
    low_fence = q1 - 1.5 * iqr
    high_fence = q3 + 1.5 * iqr
    return {
        "count": len(data),
        "mean_ns": mean,
        "median_ns": statistics.median(data),
        "standard_deviation_ns": statistics.stdev(data) if len(data) > 1 else 0.0,
        "coefficient_of_variation": (
            (statistics.stdev(data) / mean) if len(data) > 1 and mean else 0.0
        ),
        "minimum_ns": min(data),
        "maximum_ns": max(data),
        "q1_ns": q1,
        "q3_ns": q3,
        "iqr_ns": iqr,
        "p5_ns": percentile(data, 0.05),
        "p95_ns": percentile(data, 0.95),
        "throughput_images_per_second": 1_000_000_000.0 / mean if mean else None,
        "outlier_count": sum(value < low_fence or value > high_fence for value in data),
        "outlier_fences_ns": [low_fence, high_fence],
    }


def _duration_summary(values: Iterable[float]) -> dict[str, Any]:
    result = summarize_values(values)
    result.pop("throughput_images_per_second")
    return result


def bootstrap_session_medians(
    session_medians: Iterable[float],
    iterations: int = 10_000,
    seed: int = 0,
) -> dict[str, Any]:
    medians = [float(value) for value in session_medians]
    if not medians:
        raise ValueError("bootstrap requires session medians")
    rng = random.Random(seed)
    estimates = []
    for _ in range(iterations):
        sample = [rng.choice(medians) for _ in medians]
        estimates.append(statistics.median(sample))
    return {
        "method": "deterministic percentile bootstrap of independent session medians",
        "confidence": 0.95,
        "iterations": iterations,
        "seed": seed,
        "session_count": len(medians),
        "estimate_ns": statistics.median(medians),
        "lower_ns": percentile(estimates, 0.025),
        "upper_ns": percentile(estimates, 0.975),
    }


def _power_summary(root: Path) -> dict[str, Any]:
    power = root / "power-sampling"
    if (power / "unavailable.txt").is_file() or not power.is_dir():
        return {"status": "unavailable"}

    def parse(path: Path) -> list[tuple[float, float]]:
        by_time: dict[float, float] = defaultdict(float)
        if not path.is_file():
            return []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            fields = line.split(",", 2)
            if len(fields) != 3:
                continue
            by_time[float(fields[0])] += float(fields[2]) / 1_000_000.0
        return sorted(by_time.items())

    idle = parse(power / "idle-readings.csv")
    active = parse(power / "readings.csv")
    if not active:
        return {"status": "unavailable"}
    idle_watts = statistics.fmean(value for _, value in idle) if idle else None
    energy = sum(
        (active[index - 1][1] + active[index][1])
        * 0.5
        * (active[index][0] - active[index - 1][0])
        for index in range(1, len(active))
    )
    duration = active[-1][0] - active[0][0] if len(active) > 1 else 0.0
    profile = _read_profile(root / "power-1" / "profile.json")
    measured = int(profile["measured_iterations"])
    dynamic = (
        max(0.0, energy - idle_watts * duration)
        if idle_watts is not None
        else None
    )
    return {
        "status": "available",
        "idle_mean_watts": idle_watts,
        "active_duration_seconds": duration,
        "active_energy_joules": energy,
        "active_energy_joules_per_inference": energy / measured if measured else None,
        "dynamic_energy_joules": dynamic,
        "dynamic_energy_joules_per_inference": (
            dynamic / measured if dynamic is not None and measured else None
        ),
        "measured_iterations": measured,
        "raw_active_samples": len(active),
        "raw_idle_samples": len(idle),
    }


def _profile_groups(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["session"], row["profile_path"])].append(row)
    return grouped


def _phase_summary(rows: list[dict[str, Any]], regime: str) -> dict[str, Any]:
    profile_groups = _profile_groups(rows)
    representatives = [group[0] for group in profile_groups.values()]
    detailed = {
        phase: statistics.fmean(
            row[f"phase_{phase}_ns"] for row in representatives
        )
        for phase in PHASES
    }
    if regime == "steady":
        runtime_execution = statistics.fmean(
            row["runtime_execution_ns"] for row in rows
        )
        output_extraction = statistics.fmean(
            row["output_extract_ns"] for row in rows
        )
        observed = runtime_execution + output_extraction
        return {
            "scope": "per measured inference in one loaded context",
            "detailed_context_one_time_mean_ns": detailed,
            "aggregates_mean_ns": {
                "runtime_execution": runtime_execution,
                "result_handling": output_extraction,
            },
            "observed_iteration_mean_ns": observed,
            "percentages": {
                "runtime_execution": runtime_execution * 100.0 / observed,
                "result_handling": output_extraction * 100.0 / observed,
            },
            "note": (
                "Context setup, model loading, buffer binding, final DIMG writing, "
                "and teardown occur once per steady profile and are reported separately."
            ),
        }

    profile_aggregates = []
    for group in profile_groups.values():
        if len(group) != 1:
            raise ValueError(
                f"{regime} profiles must contain exactly one measured inference"
            )
        row = group[0]
        aggregates = {
            "runtime_initialization": (
                row["phase_runtime_create_ns"] + row["phase_emu_init_ns"]
            ),
            "model_loading": (
                row["phase_loadable_read_ns"] + row["phase_runtime_load_ns"]
            ),
            "buffer_preparation": (
                row["phase_input_setup_ns"] + row["phase_output_setup_ns"]
            ),
            "runtime_execution": row["runtime_execution_ns"],
            "result_handling": (
                row["output_extract_ns"] + row["phase_output_write_ns"]
            ),
            "teardown": (
                row["phase_buffer_cleanup_ns"]
                + row["phase_emu_stop_ns"]
                + row["phase_runtime_unload_ns"]
                + row["phase_runtime_destroy_ns"]
            ),
        }
        accounted = sum(aggregates.values())
        if accounted > row["latency_ns"]:
            raise ValueError(
                f"{row['profile_path']}: profiled phases exceed external "
                f"{regime} end-to-end latency"
            )
        aggregates["unprofiled_process_and_launch"] = max(
            0, row["latency_ns"] - accounted
        )
        profile_aggregates.append(aggregates)

    aggregate_names = tuple(profile_aggregates[0])
    aggregate_means = {
        name: statistics.fmean(item[name] for item in profile_aggregates)
        for name in aggregate_names
    }
    end_to_end = statistics.fmean(row["latency_ns"] for row in rows)
    return {
        "scope": "one fresh process and one measured inference",
        "detailed_profile_mean_ns": detailed,
        "aggregates_mean_ns": aggregate_means,
        "end_to_end_mean_ns": end_to_end,
        "percentages": {
            name: value * 100.0 / end_to_end if end_to_end else 0.0
            for name, value in aggregate_means.items()
        },
    }


def _scalar_summary(values: Iterable[float]) -> dict[str, Any]:
    data = [float(value) for value in values]
    if not data:
        raise ValueError("scalar summary requires at least one value")
    return {
        "count": len(data),
        "mean": statistics.fmean(data),
        "median": statistics.median(data),
        "minimum": min(data),
        "maximum": max(data),
        "p5": percentile(data, 0.05),
        "p95": percentile(data, 0.95),
    }


def _clock_equivalent_summary(
    rows: list[dict[str, Any]],
    clock_hz: int,
) -> dict[str, Any]:
    values = [
        row["runtime_execution_ns"] * clock_hz / 1_000_000_000.0
        for row in rows
    ]
    return {
        "label": "host-observed NVDLA-clock-equivalent runtime execution intervals",
        "clock_hz": clock_hz,
        "includes_software_overhead": True,
        **_scalar_summary(values),
    }


def _scheduling_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    profile_groups = list(_profile_groups(rows).values())
    profiles = [group[0] for group in profile_groups]
    per_inference = [
        {
            "user_time_ns": (
                group[0]["process_user_time_ns"]
                / group[0]["profile_total_iterations"]
            ),
            "system_time_ns": (
                group[0]["process_system_time_ns"]
                / group[0]["profile_total_iterations"]
            ),
            "voluntary_context_switches": (
                group[0]["voluntary_context_switches"]
                / group[0]["profile_total_iterations"]
            ),
            "involuntary_context_switches": (
                group[0]["involuntary_context_switches"]
                / group[0]["profile_total_iterations"]
            ),
        }
        for group in profile_groups
    ]
    return {
        "cpu_affinity": sorted({row["cpu_affinity"] for row in profiles}),
        "process_user_time_ns": _duration_summary(
            row["process_user_time_ns"] for row in profiles
        ),
        "process_system_time_ns": _duration_summary(
            row["process_system_time_ns"] for row in profiles
        ),
        "minor_page_faults": _scalar_summary(
            row["minor_page_faults"] for row in profiles
        ),
        "major_page_faults": _scalar_summary(
            row["major_page_faults"] for row in profiles
        ),
        "voluntary_context_switches": _scalar_summary(
            row["voluntary_context_switches"] for row in profiles
        ),
        "involuntary_context_switches": _scalar_summary(
            row["involuntary_context_switches"] for row in profiles
        ),
        "per_executed_inference_including_warmups": {
            "user_time_ns": _duration_summary(
                item["user_time_ns"] for item in per_inference
            ),
            "system_time_ns": _duration_summary(
                item["system_time_ns"] for item in per_inference
            ),
            "voluntary_context_switches": _scalar_summary(
                item["voluntary_context_switches"] for item in per_inference
            ),
            "involuntary_context_switches": _scalar_summary(
                item["involuntary_context_switches"] for item in per_inference
            ),
        },
        "cpu_migrations": "unavailable",
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _latency_svg(path: Path, grouped: dict[str, list[dict[str, Any]]]) -> None:
    width, height = 900, 460
    regimes = [name for name in ("cold", "warm", "steady") if name in grouped]
    all_ms = [row["latency_ns"] / 1e6 for name in regimes for row in grouped[name]]
    min_log = math.log10(max(min(all_ms), 1e-6))
    max_log = math.log10(max(all_ms))
    span = max(max_log - min_log, 0.1)
    colors = {"cold": "#c33c54", "warm": "#2f7d5c", "steady": "#3169a8"}
    body = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<text x="450" y="28" text-anchor="middle" font-family="sans-serif" font-size="18">NVDLA latency distributions</text>',
        '<text x="20" y="230" transform="rotate(-90 20 230)" text-anchor="middle" font-family="sans-serif" font-size="13">Latency (ms, log scale)</text>',
        '<line x1="70" y1="400" x2="870" y2="400" stroke="#333"/>',
        '<line x1="70" y1="45" x2="70" y2="400" stroke="#333"/>',
    ]
    for index, regime in enumerate(regimes):
        center = 170 + index * 260
        body.append(
            f'<text x="{center}" y="430" text-anchor="middle" font-family="sans-serif" font-size="14">{regime}</text>'
        )
        for point, row in enumerate(grouped[regime]):
            value = row["latency_ns"] / 1e6
            y = 400 - (math.log10(max(value, 1e-6)) - min_log) / span * 345
            x = center + ((point * 37) % 101 - 50) * 0.8
            body.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.2" fill="{colors[regime]}" fill-opacity="0.55"/>'
            )
    body.append("</svg>")
    path.write_text("\n".join(body) + "\n", encoding="utf-8")


def _phase_svg(path: Path, phases: dict[str, dict[str, Any]]) -> None:
    width, height = 900, 430
    regimes = list(phases)
    colors = {
        "runtime_initialization": "#7656a5",
        "model_loading": "#c33c54",
        "buffer_preparation": "#2f7d5c",
        "runtime_execution": "#3169a8",
        "result_handling": "#d58b28",
        "teardown": "#69737d",
        "unprofiled_process_and_launch": "#b8b8b8",
    }
    body = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<text x="450" y="28" text-anchor="middle" font-family="sans-serif" font-size="18">Mean aggregate phase composition</text>',
    ]
    selected = tuple(colors)
    for index, regime in enumerate(regimes):
        values = phases[regime]["aggregates_mean_ns"]
        total = sum(values.values())
        x = 110 + index * 260
        y = 350.0
        for name in selected:
            value = values.get(name, 0.0)
            bar = 280.0 * value / total if total else 0.0
            y -= bar
            body.append(
                f'<rect x="{x}" y="{y:.1f}" width="120" height="{bar:.1f}" fill="{colors[name]}"/>'
            )
        body.append(
            f'<text x="{x + 60}" y="378" text-anchor="middle" font-family="sans-serif" font-size="14">{regime}</text>'
        )
    legend_x = 650
    for index, name in enumerate(selected):
        y = 60 + index * 25
        body.append(f'<rect x="{legend_x}" y="{y - 12}" width="16" height="16" fill="{colors[name]}"/>')
        body.append(
            f'<text x="{legend_x + 24}" y="{y + 1}" font-family="sans-serif" font-size="12">{name}</text>'
        )
    body.append("</svg>")
    path.write_text("\n".join(body) + "\n", encoding="utf-8")


def _session_regime_values(
    grouped: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, list[float]]]:
    result: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for regime, rows in grouped.items():
        for row in rows:
            result[row["session"]][regime].append(row["latency_ns"])
    return result


def _software_overhead(
    grouped: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    session_values = _session_regime_values(grouped)
    sessions = []
    for session, regimes in sorted(session_values.items()):
        if "warm" not in regimes or "steady" not in regimes:
            continue
        warm = statistics.median(regimes["warm"])
        steady = statistics.median(regimes["steady"])
        overhead = warm - steady
        sessions.append(
            {
                "session": session,
                "warm_end_to_end_median_ns": warm,
                "runtime_execution_median_ns": steady,
                "overhead_ns": overhead,
                "overhead_percentage_of_warm_end_to_end": (
                    overhead * 100.0 / warm if warm else None
                ),
            }
        )
    if not sessions:
        return {"status": "unavailable", "reason": "warm and steady regimes required"}
    return {
        "status": "available",
        "definition": (
            "per-session warm end-to-end median minus steady runtime execution "
            "latency median"
        ),
        "sessions": sessions,
        "overhead_ns": _duration_summary(
            item["overhead_ns"] for item in sessions
        ),
        "overhead_percentage": _scalar_summary(
            item["overhead_percentage_of_warm_end_to_end"] for item in sessions
        ),
    }


def _pilot_sanity(
    grouped: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    session_values = _session_regime_values(grouped)
    cache_effect = []
    for session, regimes in sorted(session_values.items()):
        if "cold" not in regimes or "warm" not in regimes:
            continue
        cold = statistics.median(regimes["cold"])
        warm = statistics.median(regimes["warm"])
        cache_effect.append(
            {
                "session": session,
                "cold_median_ns": cold,
                "warm_median_ns": warm,
                "cold_minus_warm_ns": cold - warm,
                "cold_penalty_percentage_of_warm": (
                    (cold - warm) * 100.0 / warm if warm else None
                ),
            }
        )

    first_later = []
    steady_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in grouped.get("steady", []):
        steady_by_session[row["session"]].append(row)
    for session, rows in sorted(steady_by_session.items()):
        ordered = sorted(rows, key=lambda row: row["sample_index"])
        if len(ordered) < 2:
            continue
        first = ordered[0]["runtime_execution_ns"]
        later = statistics.median(
            row["runtime_execution_ns"] for row in ordered[1:]
        )
        first_later.append(
            {
                "session": session,
                "first_runtime_execution_ns": first,
                "later_runtime_execution_median_ns": later,
                "first_minus_later_ns": first - later,
                "first_penalty_percentage_of_later": (
                    (first - later) * 100.0 / later if later else None
                ),
            }
        )
    return {
        "cold_vs_cached": cache_effect,
        "first_vs_later_steady_inference": first_later,
        "external_ab_controls": {
            "quiet_vs_verbose": (
                "analyze as separate firmware_log=0 and firmware_log=1 pilot reports"
            ),
            "profiled_vs_legacy": (
                "run as a separate short control; do not mix with primary archives"
            ),
        },
    }


def _throughput_definitions(summaries: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    labels = {
        "cold": "cold_deployment_images_per_second",
        "warm": "warm_end_to_end_images_per_second",
        "steady": "steady_runtime_execution_images_per_second",
    }
    for regime, label in labels.items():
        if regime in summaries:
            result[label] = summaries[regime]["latency"][
                "throughput_images_per_second"
            ]
    if "warm" in summaries and "steady" in summaries:
        input_preparation = summaries["warm"]["phases"][
            "detailed_profile_mean_ns"
        ]["input_setup"]
        runtime_execution = summaries["steady"]["latency"]["mean_ns"]
        bottleneck = max(input_preparation, runtime_execution)
        result["theoretical_stage_bottleneck_upper_bound_images_per_second"] = (
            1_000_000_000.0 / bottleneck if bottleneck else None
        )
        result["theoretical_stage_bottleneck_basis"] = {
            "warm_input_preparation_mean_ns": input_preparation,
            "steady_runtime_execution_mean_ns": runtime_execution,
            "formula": "1 / max(input preparation, runtime execution)",
            "qualification": (
                "Analytical upper bound only. The current blocking runtime does not "
                "demonstrate overlap, and steady tests reuse one prepared input."
            ),
        }
    return result


def import_performance_archives(archives: list[Path], out_dir: Path) -> int:
    if not archives:
        raise ValueError("at least one benchmark archive is required")
    out_dir.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    baseline: dict[str, Any] | None = None

    with tempfile.TemporaryDirectory() as temporary:
        extraction_root = Path(temporary)
        for index, archive in enumerate(archives):
            root = _load_session_root(archive, extraction_root / str(index))
            env = _parse_env(root / "benchmark.env")
            if env.get("status") != "0":
                raise ValueError(f"{archive}: target benchmark did not pass")
            if env.get("classification") != "exact-performance-pass":
                raise ValueError(
                    f"{archive}: target benchmark is not correctness-qualified"
                )
            provenance = _provenance(root, env)
            if baseline is None:
                baseline = provenance
            else:
                differences = [
                    key
                    for key in PROVENANCE_KEYS
                    if provenance.get(key) != baseline.get(key)
                ]
                if differences:
                    raise ValueError(
                        "mixed benchmark provenance: " + ", ".join(differences)
                    )
            session_id = root.name
            rows = _profile_rows(
                root,
                session_id,
                provenance["model"],
                provenance["benchmark_cpu"],
            )
            all_rows.extend(rows)
            sessions.append(
                {
                    "session": session_id,
                    "archive": str(archive),
                    "archive_sha256": sha256_file(archive),
                    "provenance": provenance,
                    "power": _power_summary(root),
                }
            )

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in all_rows:
        grouped[row["regime"]].append(row)
    summaries: dict[str, Any] = {}
    for regime, rows in grouped.items():
        session_groups: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            session_groups[row["session"]].append(row["latency_ns"])
        session_results = {
            session: summarize_values(values)
            for session, values in sorted(session_groups.items())
        }
        summaries[regime] = {
            "latency": summarize_values(row["latency_ns"] for row in rows),
            "runtime_execution": summarize_values(
                row["runtime_execution_ns"] for row in rows
            ),
            "runtime_execution_clock_equivalent_intervals": (
                _clock_equivalent_summary(rows, baseline["nvdla_clock_hz"])
            ),
            "phases": _phase_summary(rows, regime),
            "scheduling": _scheduling_summary(rows),
            "maximum_clock_overhead_fraction": max(
                row["clock_overhead_fraction"] for row in rows
            ),
            "sessions": session_results,
            "session_median_bootstrap_95ci": bootstrap_session_medians(
                result["median_ns"] for result in session_results.values()
            ),
        }

    summary = {
        "schema_version": 2,
        "provenance": baseline,
        "workload_complexity": baseline["workload_complexity"],
        "session_count": len(sessions),
        "sessions": sessions,
        "regimes": summaries,
        "throughput_definitions": _throughput_definitions(summaries),
        "software_overhead": _software_overhead(grouped),
        "pilot_sanity": _pilot_sanity(grouped),
        "correctness_qualification": {
            "status": "qualified",
            "accepted_samples": len(all_rows),
            "requirements": [
                "target benchmark status is exact-performance-pass",
                "every output matches the established exact golden",
                "repeated in-memory outputs are identical",
                "every run has a positive NVDLA IRQ delta",
                "no configured bad kernel pattern is present",
                "the active NVDLA clock matches the XSA-derived rate",
            ],
        },
        "outlier_policy": (
            "No samples are discarded. Tukey 1.5 IQR fences flag retained observations."
        ),
        "timing_boundaries": {
            "cold": "CLOCK_MONOTONIC_RAW parent launch-to-process-exit after page-cache drop",
            "warm": "CLOCK_MONOTONIC_RAW parent launch-to-process-exit with primed file cache",
            "steady": (
                "runtime execution latency: blocking nvdla IRuntime::submit() "
                "with one loaded and bound context"
            ),
        },
    }
    _write_csv(out_dir / "performance-raw.csv", all_rows)
    write_json(out_dir / "performance-summary.json", summary)

    summary_rows = []
    for regime, result in summaries.items():
        summary_rows.append(
            {
                "model": baseline["model"] if baseline else "",
                "regime": regime,
                **result["latency"],
                "runtime_execution_mean_ns": result["runtime_execution"]["mean_ns"],
                "runtime_execution_median_ns": result["runtime_execution"]["median_ns"],
                "clock_equivalent_mean_intervals": result[
                    "runtime_execution_clock_equivalent_intervals"
                ]["mean"],
                "bootstrap_lower_ns": result["session_median_bootstrap_95ci"]["lower_ns"],
                "bootstrap_upper_ns": result["session_median_bootstrap_95ci"]["upper_ns"],
                "session_count": result["session_median_bootstrap_95ci"]["session_count"],
            }
        )
    _write_csv(out_dir / "performance-summary.csv", summary_rows)
    _latency_svg(out_dir / "latency-distribution.svg", grouped)
    _phase_svg(
        out_dir / "phase-breakdown.svg",
        {regime: result["phases"] for regime, result in summaries.items()},
    )

    report = [
        f"# NVDLA {baseline['model']} Performance Report",
        "",
        f"- Independent fresh-boot sessions: {len(sessions)}",
        f"- Kernel: `{baseline['kernel_release']}`",
        f"- NVDLA clock: `{baseline['nvdla_clock_hz']}` Hz observed; "
        f"`{baseline['nvdla_clock_expected_hz']}` Hz expected from XSA "
        f"(tolerance `{baseline['nvdla_clock_tolerance_hz']}` Hz)",
        f"- Module SHA256: `{baseline['module_sha256']}`",
        f"- Runtime SHA256: `{baseline['runtime_sha256']}`",
        f"- Benchmark CPU affinity: `{baseline['benchmark_cpu']}`",
        f"- Workload: `{baseline['workload_complexity']['loadable_size_bytes']}` byte "
        f"loadable, input NCHW `{baseline['workload_complexity']['input_shape_nchw']}`, "
        f"`{baseline['workload_complexity']['hwl_count']}` HWLs",
        f"- Engine operations: `{baseline['workload_complexity']['operation_counts']}`",
        f"- Maximum timing-pair overhead: "
        f"`{max(result['maximum_clock_overhead_fraction'] for result in summaries.values()) * 100:.6f}%` "
        "of runtime execution latency",
        "",
        "All accepted samples are correctness-qualified by exact golden output, "
        "output consistency, positive IRQ progress, clean kernel logs, and verified clock.",
        "No observations were discarded. Reported outliers remain in every statistic.",
        "",
        "## Latency",
        "",
        "| Regime | n | Mean (ms) | Median (ms) | SD (ms) | CV | p5 (ms) | p95 (ms) | Images/s | Outliers | Session median 95% CI (ms) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for regime in ("cold", "warm", "steady"):
        if regime not in summaries:
            continue
        stats = summaries[regime]["latency"]
        ci = summaries[regime]["session_median_bootstrap_95ci"]
        report.append(
            f"| {regime} | {stats['count']} | {stats['mean_ns'] / 1e6:.3f} | "
            f"{stats['median_ns'] / 1e6:.3f} | {stats['standard_deviation_ns'] / 1e6:.3f} | "
            f"{stats['coefficient_of_variation']:.4f} | {stats['p5_ns'] / 1e6:.3f} | "
            f"{stats['p95_ns'] / 1e6:.3f} | {stats['throughput_images_per_second']:.4f} | "
            f"{stats['outlier_count']} | {ci['lower_ns'] / 1e6:.3f} to {ci['upper_ns'] / 1e6:.3f} |"
        )
    report.extend(
        [
            "",
            "## Throughput Definitions",
            "",
            "| Definition | Images/s |",
            "|---|---:|",
        ]
    )
    throughput = summary["throughput_definitions"]
    for name in (
        "cold_deployment_images_per_second",
        "warm_end_to_end_images_per_second",
        "steady_runtime_execution_images_per_second",
        "theoretical_stage_bottleneck_upper_bound_images_per_second",
    ):
        if name in throughput:
            report.append(f"| {name.replace('_', ' ')} | {throughput[name]:.4f} |")
    if "theoretical_stage_bottleneck_basis" in throughput:
        report.extend(
            [
                "",
                "The stage-bottleneck value is an analytical upper bound, not measured "
                "pipelined throughput. The current blocking runtime does not demonstrate "
                "input preparation overlapping runtime execution.",
            ]
        )

    report.extend(
        [
            "",
            "## Runtime Execution",
            "",
            "`runtime execution latency` is the blocking `IRuntime::submit()` interval.",
            "",
            "| Regime | Mean (ms) | Median (ms) | Mean clock-equivalent intervals |",
            "|---|---:|---:|---:|",
        ]
    )
    for regime in ("cold", "warm", "steady"):
        if regime not in summaries:
            continue
        execution = summaries[regime]["runtime_execution"]
        intervals = summaries[regime][
            "runtime_execution_clock_equivalent_intervals"
        ]
        report.append(
            f"| {regime} | {execution['mean_ns'] / 1e6:.3f} | "
            f"{execution['median_ns'] / 1e6:.3f} | {intervals['mean']:.1f} |"
        )
    report.extend(
        [
            "",
            "Clock-equivalent intervals are host-observed runtime execution latency "
            "multiplied by the measured NVDLA clock. They include UMD, ioctl, scheduling, "
            "IRQ, and emulator overhead and are not hardware cycle counts.",
            "",
            "## Aggregate Phases",
            "",
            "| Regime | Initialization (ms) | Model loading (ms) | Buffer preparation (ms) | Runtime execution (ms) | Result handling (ms) | Teardown (ms) | Unprofiled (ms) |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for regime in ("cold", "warm", "steady"):
        if regime not in summaries:
            continue
        phases = summaries[regime]["phases"]["aggregates_mean_ns"]
        report.append(
            f"| {regime} | {phases.get('runtime_initialization', 0) / 1e6:.3f} | "
            f"{phases.get('model_loading', 0) / 1e6:.3f} | "
            f"{phases.get('buffer_preparation', 0) / 1e6:.3f} | "
            f"{phases.get('runtime_execution', 0) / 1e6:.3f} | "
            f"{phases.get('result_handling', 0) / 1e6:.3f} | "
            f"{phases.get('teardown', 0) / 1e6:.3f} | "
            f"{phases.get('unprofiled_process_and_launch', 0) / 1e6:.3f} |"
        )

    overhead = summary["software_overhead"]
    if overhead["status"] == "available":
        report.extend(
            [
                "",
                "## Software Overhead",
                "",
                "Per-session overhead is the warm end-to-end median minus the steady "
                "runtime execution median.",
                "",
                f"- Median overhead: `{overhead['overhead_ns']['median_ns'] / 1e6:.3f}` ms",
                f"- Median share of warm end-to-end latency: "
                f"`{overhead['overhead_percentage']['median']:.3f}%`",
            ]
        )

    report.extend(
        [
            "",
            "## Scheduling Context",
            "",
            f"The runtime was pinned to CPU `{baseline['benchmark_cpu']}`. Per-process "
            "user/system CPU time, page faults, and voluntary/involuntary context "
            "switches are retained in the JSON summary and raw CSV. CPU migration "
            "counts are marked unavailable rather than inferred.",
            "",
            "## Figures",
            "",
            "![Latency distribution](latency-distribution.svg)",
            "",
            "![Phase breakdown](phase-breakdown.svg)",
            "",
            "## Timing Boundaries",
            "",
            "Cold and warm results use the external launch-to-exit clock. Steady-state "
            "results use runtime execution latency (blocking `IRuntime::submit()`), "
            "which includes UMD submission, "
            "the KMD ioctl and scheduler, hardware execution, and IRQ completion.",
            "",
            "Power results, when available, come from a separate run and are not mixed "
            "with the primary latency observations.",
        ]
    )
    (out_dir / "performance-report.md").write_text(
        "\n".join(report) + "\n", encoding="utf-8"
    )
    print(f"Performance report: {out_dir / 'performance-report.md'}")
    return 0
