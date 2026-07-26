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
PROVENANCE_KEYS = (
    "model",
    "input_sha256",
    "loadable_sha256",
    "module_sha256",
    "runtime_sha256",
    "runtime_library_sha256",
    "kernel_release",
    "nvdla_clock_hz",
    "payload_sha256",
    "firmware_log",
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


def _clock_rate(root: Path) -> int | None:
    clock_path = root / "nvdla-clock-lines.txt"
    if not clock_path.is_file():
        return None
    for line in clock_path.read_text(encoding="utf-8", errors="replace").splitlines():
        for field in line.split():
            if field.isdigit() and 99_999_000 <= int(field) <= 100_001_000:
                return int(field)
    return None


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
    payload_hash = software.get("SHA256SUMS")
    rate = _clock_rate(root)
    result = {
        "model": env.get("model"),
        "input_sha256": str(image.get("sha256", "")).lower() or None,
        "loadable_sha256": str(loadable.get("sha256", "")).lower() or None,
        "module_sha256": next(iter(module.values()), None),
        "runtime_sha256": software.get("nvdla_runtime"),
        "runtime_library_sha256": software.get("libnvdla_runtime.so"),
        "kernel_release": kernel_release,
        "nvdla_clock_hz": rate,
        "payload_sha256": payload_hash,
        "firmware_log": env.get("firmware_log"),
    }
    missing = [key for key in PROVENANCE_KEYS if result.get(key) in {None, ""}]
    if missing:
        raise ValueError(f"incomplete benchmark provenance: {', '.join(missing)}")
    return result


def _read_profile(path: Path) -> dict[str, Any]:
    profile = read_json(path)
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


def _profile_rows(root: Path, session: str, model: str) -> list[dict[str, Any]]:
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
                submit_ns = int(sample["submit_ns"])
                if submit_ns <= 0:
                    raise ValueError(f"{profile_path}: invalid submit latency")
                overhead_fraction = clock_overhead_ns / submit_ns
                if overhead_fraction > 0.01:
                    raise ValueError(
                        f"{profile_path}: clock measurement overhead exceeds 1% "
                        f"of submit latency"
                    )
                latency_ns = launch_ns if regime in {"cold", "warm"} else submit_ns
                row: dict[str, Any] = {
                    "session": session,
                    "model": model,
                    "regime": regime,
                    "run": run_dir.name,
                    "sample_index": int(sample["index"]),
                    "latency_ns": latency_ns,
                    "submit_ns": submit_ns,
                    "output_extract_ns": int(sample["output_extract_ns"]),
                    "launch_elapsed_ns": launch_ns,
                    "process_total_ns": int(phases["process_total"]),
                    "clock_resolution_ns": int(profile["clock_resolution_ns"]),
                    "clock_pair_overhead_ns": clock_overhead_ns,
                    "clock_overhead_fraction": overhead_fraction,
                    "profile_path": str(profile_path.relative_to(root)),
                }
                for phase in PHASES:
                    row[f"phase_{phase}_ns"] = int(phases.get(phase, 0))
                row["outside_submit_ns"] = max(0, int(latency_ns) - submit_ns)
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


def _phase_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        unique[(row["session"], row["profile_path"])] = row
    profiles = list(unique.values())
    result: dict[str, Any] = {}
    for phase in PHASES:
        result[phase] = statistics.fmean(
            row[f"phase_{phase}_ns"] for row in profiles
        )
    result["submit"] = statistics.fmean(row["submit_ns"] for row in rows)
    total = sum(result.values())
    result["percentages"] = {
        name: value * 100.0 / total if total else 0.0
        for name, value in result.items()
        if name != "percentages"
    }
    result["runtime_overhead_outside_submit_ns"] = statistics.fmean(
        row["outside_submit_ns"] for row in rows
    )
    return result


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
        "submit": "#3169a8",
        "runtime_load": "#c33c54",
        "input_setup": "#2f7d5c",
        "output_setup": "#d58b28",
        "loadable_read": "#7656a5",
        "other": "#8a8a8a",
    }
    body = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<text x="450" y="28" text-anchor="middle" font-family="sans-serif" font-size="18">Mean measured phase composition</text>',
    ]
    selected = ("submit", "runtime_load", "input_setup", "output_setup", "loadable_read")
    for index, regime in enumerate(regimes):
        values = phases[regime]
        total = sum(values[name] for name in selected)
        other = sum(values[name] for name in PHASES if name not in selected)
        total += other
        x = 110 + index * 260
        y = 350.0
        for name, value in [*( (name, values[name]) for name in selected), ("other", other)]:
            bar = 280.0 * value / total if total else 0.0
            y -= bar
            body.append(
                f'<rect x="{x}" y="{y:.1f}" width="120" height="{bar:.1f}" fill="{colors[name]}"/>'
            )
        body.append(
            f'<text x="{x + 60}" y="378" text-anchor="middle" font-family="sans-serif" font-size="14">{regime}</text>'
        )
    legend_x = 650
    for index, name in enumerate((*selected, "other")):
        y = 75 + index * 28
        body.append(f'<rect x="{legend_x}" y="{y - 12}" width="16" height="16" fill="{colors[name]}"/>')
        body.append(
            f'<text x="{legend_x + 24}" y="{y + 1}" font-family="sans-serif" font-size="12">{name}</text>'
        )
    body.append("</svg>")
    path.write_text("\n".join(body) + "\n", encoding="utf-8")


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
            rows = _profile_rows(root, session_id, provenance["model"])
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
            "submit": summarize_values(row["submit_ns"] for row in rows),
            "phases": _phase_summary(rows),
            "maximum_clock_overhead_fraction": max(
                row["clock_overhead_fraction"] for row in rows
            ),
            "sessions": session_results,
            "session_median_bootstrap_95ci": bootstrap_session_medians(
                result["median_ns"] for result in session_results.values()
            ),
        }

    summary = {
        "schema_version": 1,
        "provenance": baseline,
        "session_count": len(sessions),
        "sessions": sessions,
        "regimes": summaries,
        "outlier_policy": (
            "No samples are discarded. Tukey 1.5 IQR fences flag retained observations."
        ),
        "timing_boundaries": {
            "cold": "CLOCK_MONOTONIC_RAW parent launch-to-process-exit after page-cache drop",
            "warm": "CLOCK_MONOTONIC_RAW parent launch-to-process-exit with primed file cache",
            "steady": "blocking nvdla IRuntime::submit() with one loaded and bound context",
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
        f"- NVDLA clock: `{baseline['nvdla_clock_hz']}` Hz",
        f"- Module SHA256: `{baseline['module_sha256']}`",
        f"- Runtime SHA256: `{baseline['runtime_sha256']}`",
        f"- Maximum timing-pair overhead: "
        f"`{max(result['maximum_clock_overhead_fraction'] for result in summaries.values()) * 100:.6f}%` "
        "of submit latency",
        "",
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
            "## Figures",
            "",
            "![Latency distribution](latency-distribution.svg)",
            "",
            "![Phase breakdown](phase-breakdown.svg)",
            "",
            "## Timing Boundaries",
            "",
            "Cold and warm results use the external launch-to-exit clock. Steady-state "
            "results use blocking `IRuntime::submit()`, which includes UMD submission, "
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
