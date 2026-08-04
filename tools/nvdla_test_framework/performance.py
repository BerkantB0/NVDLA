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
    "benchmark_interface",
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
    "power_sample",
    "power_sampler_sha256",
    "power_interval_ms",
    "power_phase",
    "power_sampler_cpu",
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
        "benchmark_interface": env.get("benchmark_interface"),
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
        "power_sample": env.get("power_sample", "0"),
        "power_sampler_sha256": (
            software.get("nvdla-power-sampler")
            if env.get("power_sample", "0") == "1"
            else "disabled"
        ),
        "power_interval_ms": (
            env.get("power_interval_ms")
            if env.get("power_sample", "0") == "1"
            else "disabled"
        ),
        "power_phase": (
            env.get("power_phase")
            if env.get("power_sample", "0") == "1"
            else "disabled"
        ),
        "power_sampler_cpu": (
            env.get("power_sampler_cpu")
            if env.get("power_sample", "0") == "1"
            else "disabled"
        ),
        "workload_complexity": complexity,
    }
    missing = [key for key in PROVENANCE_KEYS if result.get(key) in {None, ""}]
    if missing:
        raise ValueError(f"incomplete benchmark provenance: {', '.join(missing)}")
    return result


def _environment_evidence(root: Path, env: dict[str, str]) -> dict[str, Any]:
    if env.get("schema_version") != "2":
        raise ValueError("unsupported benchmark environment schema")
    boot_id = env.get("boot_id", "")
    ntp_synchronized = env.get("ntp_synchronized", "")
    temperature_before = env.get("temperature_before_status", "")
    temperature_after = env.get("temperature_after_status", "")
    temperature_before_count = int(
        env.get("temperature_before_sensor_count", "-1")
    )
    temperature_after_count = int(
        env.get("temperature_after_sensor_count", "-1")
    )
    timestamp_utc = env.get("timestamp_utc", "")
    boot_id_file = (root / "boot-id.txt").read_text(
        encoding="utf-8", errors="replace"
    ).strip()
    time_sync = _parse_env(root / "time-sync.env")
    if not boot_id or boot_id != boot_id_file:
        raise ValueError("missing or inconsistent Linux boot ID evidence")
    if time_sync.get("boot_id") != boot_id:
        raise ValueError("time synchronization evidence has a different boot ID")
    if not ntp_synchronized or time_sync.get("ntp_synchronized") != ntp_synchronized:
        raise ValueError("missing or inconsistent NTP status evidence")
    for phase, status, count in (
        ("before", temperature_before, temperature_before_count),
        ("after", temperature_after, temperature_after_count),
    ):
        if status not in {"available", "unavailable"}:
            raise ValueError(f"temperature {phase} availability was not recorded")
        recorded = _parse_env(root / f"temperature-{phase}.env")
        if recorded.get("status") != status or int(
            recorded.get("sensor_count", "-1")
        ) != count:
            raise ValueError(f"inconsistent temperature {phase} evidence")
        if (status == "available" and count <= 0) or (
            status == "unavailable" and count != 0
        ):
            raise ValueError(f"invalid temperature {phase} sensor count")
    if not timestamp_utc:
        raise ValueError("benchmark UTC timestamp is missing")
    return {
        "boot_id": boot_id,
        "ntp_synchronized": ntp_synchronized,
        "timestamp_utc": timestamp_utc,
        "temperature_before_status": temperature_before,
        "temperature_after_status": temperature_after,
        "temperature_before_sensor_count": temperature_before_count,
        "temperature_after_sensor_count": temperature_after_count,
    }


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

    def parse(path: Path) -> dict[str, list[tuple[int, float]]]:
        if not path.is_file():
            return {}
        lines = [
            line
            for line in path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
            if line and not line.startswith("#")
        ]
        if not lines:
            return {}
        reader = csv.DictReader(lines)
        expected = {
            "sample_index",
            "timestamp_ns",
            "domain",
            "rail",
            "power_uw",
        }
        if set(reader.fieldnames or ()) != expected:
            raise ValueError(f"{path}: unsupported power sampler schema")
        sweeps: dict[int, dict[str, Any]] = {}
        for row in reader:
            sample = int(row["sample_index"])
            timestamp = int(row["timestamp_ns"])
            domain = row["domain"]
            rail = row["rail"]
            watts = int(row["power_uw"]) / 1_000_000.0
            sweep = sweeps.setdefault(
                sample,
                {"timestamp": timestamp, "domains": defaultdict(float), "rails": {}},
            )
            if not math.isclose(sweep["timestamp"], timestamp):
                raise ValueError(f"{path}: inconsistent sweep timestamp")
            rail_key = f"{domain}:{rail}"
            if rail_key in sweep["rails"]:
                raise ValueError(f"{path}: duplicate rail {rail_key} in one sweep")
            sweep["rails"][rail_key] = watts
            sweep["domains"][domain] += watts

        rail_sets = {frozenset(sweep["rails"]) for sweep in sweeps.values()}
        if len(rail_sets) != 1:
            raise ValueError(f"{path}: power rail set changed during sampling")
        series: dict[str, list[tuple[int, float]]] = defaultdict(list)
        for sample in sorted(sweeps):
            sweep = sweeps[sample]
            timestamp = sweep["timestamp"]
            for rail, watts in sweep["rails"].items():
                series[f"rail/{rail}"].append((timestamp, watts))
            for domain, watts in sweep["domains"].items():
                series[f"domain/{domain}"].append((timestamp, watts))
            series["domain/MONITORED"].append(
                (timestamp, sum(sweep["rails"].values()))
            )
        return dict(series)

    def integrate(series: list[tuple[int, float]]) -> float:
        return sum(
            (series[index - 1][1] + series[index][1])
            * 0.5
            * (series[index][0] - series[index - 1][0])
            / 1_000_000_000.0
            for index in range(1, len(series))
        )

    def interpolate(series: list[tuple[int, float]], timestamp: int) -> float:
        for left, right in zip(series, series[1:]):
            if timestamp == left[0]:
                return left[1]
            if left[0] < timestamp < right[0]:
                fraction = (timestamp - left[0]) / (right[0] - left[0])
                return left[1] + fraction * (right[1] - left[1])
        if timestamp == series[-1][0]:
            return series[-1][1]
        raise ValueError("power integration boundary is outside the sampled interval")

    def clip(
        series: list[tuple[int, float]], start_ns: int, end_ns: int
    ) -> list[tuple[int, float]]:
        if start_ns < series[0][0] or end_ns > series[-1][0]:
            raise ValueError(
                "active power trace does not bracket the benchmark launcher interval"
            )
        clipped = [(start_ns, interpolate(series, start_ns))]
        clipped.extend(
            sample for sample in series if start_ns < sample[0] < end_ns
        )
        if end_ns != start_ns:
            clipped.append((end_ns, interpolate(series, end_ns)))
        return clipped

    idle = parse(power / "idle-readings.csv")
    active = parse(power / "readings.csv")
    if not active or "domain/MONITORED" not in active:
        return {"status": "unavailable"}
    if set(idle) != set(active):
        raise ValueError("idle and active power rail sets differ")
    if "domain/PS" not in active or "domain/PL" not in active:
        raise ValueError("power evidence must contain both PS and PL domains")

    profile = _read_profile(root / "steady-1" / "profile.json")
    measured = int(profile["measured_iterations"])
    warmups = int(profile["warmup_iterations"])
    executed = measured + warmups
    interval = _parse_env(root / "steady-1" / "launch-interval.env")
    if (
        interval.get("schema_version") != "1"
        or interval.get("clock") != "CLOCK_MONOTONIC_RAW"
    ):
        raise ValueError("missing or unsupported benchmark launcher interval")
    start_ns = int(interval.get("start_ns", "0"))
    end_ns = int(interval.get("end_ns", "0"))
    elapsed_ns = int(interval.get("elapsed_ns", "0"))
    if start_ns <= 0 or end_ns <= start_ns or elapsed_ns != end_ns - start_ns:
        raise ValueError("invalid benchmark launcher interval")
    elapsed_file = int(
        (root / "steady-1" / "launch-elapsed-ns.txt").read_text().strip()
    )
    if elapsed_file != elapsed_ns:
        raise ValueError("launcher interval and elapsed-time evidence differ")

    def summarize_scope(scope: str) -> dict[str, Any]:
        idle_series = idle[scope]
        raw_active_series = active[scope]
        if len(idle_series) < 2 or len(raw_active_series) < 2:
            raise ValueError(f"insufficient power samples for {scope}")
        if any(
            right[0] <= left[0]
            for left, right in zip(idle_series, idle_series[1:])
        ) or any(
            right[0] <= left[0]
            for left, right in zip(raw_active_series, raw_active_series[1:])
        ):
            raise ValueError(f"non-monotonic power timestamps for {scope}")
        active_series = clip(raw_active_series, start_ns, end_ns)
        idle_duration_ns = idle_series[-1][0] - idle_series[0][0]
        duration_ns = end_ns - start_ns
        if idle_duration_ns <= 0 or duration_ns <= 0:
            raise ValueError(f"non-positive active duration for {scope}")
        idle_mean = integrate(idle_series) / (
            idle_duration_ns / 1_000_000_000.0
        )
        energy = integrate(active_series)
        duration = duration_ns / 1_000_000_000.0
        active_mean = energy / duration
        dynamic_mean = active_mean - idle_mean
        dynamic_energy = energy - idle_mean * duration
        return {
            "idle_mean_watts": idle_mean,
            "active_mean_watts": active_mean,
            "incremental_mean_watts": dynamic_mean,
            "active_energy_joules": energy,
            "active_energy_joules_per_inference": (
                energy / executed if executed else None
            ),
            "incremental_energy_joules": dynamic_energy,
            "incremental_energy_joules_per_inference": (
                dynamic_energy / executed if executed else None
            ),
            "inferences_per_active_joule": (
                executed / energy if energy > 0 and executed else None
            ),
            "active_duration_seconds": duration,
            "active_samples": len(raw_active_series),
            "integration_samples": len(active_series),
            "idle_samples": len(idle_series),
        }

    domains = {
        scope.removeprefix("domain/"): summarize_scope(scope)
        for scope in sorted(active)
        if scope.startswith("domain/")
    }
    rails = {
        scope.removeprefix("rail/"): summarize_scope(scope)
        for scope in sorted(active)
        if scope.startswith("rail/")
    }
    return {
        "status": "available",
        "schema_version": 3,
        "integration": {
            "method": "linear-boundary-interpolation and trapezoidal integration",
            "clock": "CLOCK_MONOTONIC_RAW",
            "launcher_start_ns": start_ns,
            "launcher_end_ns": end_ns,
            "launcher_duration_ns": elapsed_ns,
            "raw_trace_start_ns": active["domain/MONITORED"][0][0],
            "raw_trace_end_ns": active["domain/MONITORED"][-1][0],
            "pre_boundary_margin_ns": (
                start_ns - active["domain/MONITORED"][0][0]
            ),
            "post_boundary_margin_ns": (
                active["domain/MONITORED"][-1][0] - end_ns
            ),
        },
        "measurement_scope": (
            "sampled concurrently and integrated over the exact steady process "
            "launch-through-exit interval; model setup and teardown are amortized "
            "across all executed inferences"
        ),
        "power_phase": "steady",
        "idle_scope": "driver-loaded board idle without a runtime process",
        "measured_iterations": measured,
        "warmup_iterations": warmups,
        "executed_iterations": executed,
        "domains": domains,
        "rails": rails,
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


_REGIME_COLORS = {
    "cold": "#C44E52",
    "warm": "#2A7F62",
    "steady": "#2B6EA6",
}
_REGIME_LABELS = {
    "cold": ("Cold deployment", "new process; files read from storage"),
    "warm": ("Warm deployment", "new process; files already cached"),
    "steady": ("Repeated inference", "model and buffers remain resident"),
}
_PHASE_COLORS = {
    "runtime_initialization": "#6F4E7C",
    "model_loading": "#D55E00",
    "buffer_preparation": "#009E73",
    "runtime_execution": "#0072B2",
    "result_handling": "#E69F00",
    "teardown": "#5B6770",
    "unprofiled_process_and_launch": "#B8BEC5",
}
_PHASE_LABELS = {
    "runtime_initialization": "Create runtime and emulator",
    "model_loading": "Read and prepare model",
    "buffer_preparation": "Prepare input/output buffers",
    "runtime_execution": "Execute accelerator workload",
    "result_handling": "Extract and write result",
    "teardown": "Unload and clean up",
    "unprofiled_process_and_launch": "Process launch and other overhead",
}
_SVG_FONT = "Arial, Helvetica, sans-serif"
_SVG_TEXT = "#17212B"
_SVG_MUTED = "#5F6B76"
_SVG_GRID = "#D9DEE3"


def _log_ticks(low: float, high: float) -> list[float]:
    if low <= 0 or high <= 0:
        return []
    ticks = []
    for exponent in range(
        math.floor(math.log10(low)) - 1,
        math.ceil(math.log10(high)) + 2,
    ):
        for multiplier in (1.0, 2.0, 5.0):
            tick = multiplier * (10.0**exponent)
            if low <= tick <= high:
                ticks.append(tick)
    return ticks


def _format_ms(value: float) -> str:
    if value >= 1:
        return f"{value:,.3f}"
    return f"{value:.4f}"


def _format_axis_ms(value: float, step: float) -> str:
    if step >= 1:
        decimals = 0
    else:
        decimals = max(0, min(4, -math.floor(math.log10(step))))
        scaled = step * (10**decimals)
        if not math.isclose(scaled, round(scaled), rel_tol=0, abs_tol=1e-9):
            decimals = min(decimals + 1, 4)
    if decimals == 0:
        return f"{value:,.0f}"
    return f"{value:.{decimals}f}"


def _format_rate(value: float) -> str:
    if value >= 1000:
        return f"{value:,.0f}"
    if value >= 100:
        return f"{value:.1f}"
    if value >= 10:
        return f"{value:.2f}"
    return f"{value:.3f}"


def _linear_ticks(maximum: float, target_count: int = 5) -> list[float]:
    if maximum <= 0:
        return [0.0, 1.0]
    rough_step = maximum / target_count
    magnitude = 10.0 ** math.floor(math.log10(rough_step))
    normalized = rough_step / magnitude
    if normalized <= 1:
        step = magnitude
    elif normalized <= 2:
        step = 2 * magnitude
    elif normalized <= 5:
        step = 5 * magnitude
    else:
        step = 10 * magnitude
    axis_maximum = math.ceil(maximum / step) * step
    return [index * step for index in range(int(round(axis_maximum / step)) + 1)]


def _latency_svg(path: Path, grouped: dict[str, list[dict[str, Any]]]) -> None:
    width, height = 1120, 610
    regimes = [name for name in ("cold", "warm", "steady") if grouped.get(name)]
    body = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description">',
        '<title id="title">How repeatable is each latency measurement?</title>',
        '<desc id="description">Three independently scaled panels show every observation, the median, the interquartile range, and the fifth to ninety-fifth percentile range.</desc>',
        '<rect width="100%" height="100%" fill="#FFFFFF"/>',
        f'<text x="40" y="42" font-family="{_SVG_FONT}" font-size="24" font-weight="700" fill="{_SVG_TEXT}">How repeatable is each latency measurement?</text>',
        f'<text x="40" y="70" font-family="{_SVG_FONT}" font-size="13" fill="{_SVG_MUTED}">Each panel expands its own observed range so small run-to-run differences remain visible.</text>',
    ]
    panel_width = 340.0
    gap = 24.0
    first_left = 36.0
    plot_top, plot_bottom = 145.0, 430.0
    for index, regime in enumerate(regimes):
        panel_left = first_left + index * (panel_width + gap)
        plot_left = panel_left + 62
        plot_right = panel_left + panel_width - 18
        center = (plot_left + plot_right) / 2
        values = sorted(row["latency_ns"] / 1e6 for row in grouped[regime])
        observed_min = min(values)
        observed_max = max(values)
        median = percentile(values, 0.50)
        observed_span = observed_max - observed_min
        padding = max(observed_span * 0.35, abs(median) * 0.005, 0.001)
        if len(values) == 1:
            padding = max(abs(median) * 0.05, 0.001)
        lower = max(0.0, observed_min - padding)
        upper = observed_max + padding
        scale_span = max(upper - lower, 1e-9)

        def y_position(value: float) -> float:
            return plot_bottom - (value - lower) / scale_span * (
                plot_bottom - plot_top
            )

        q1 = percentile(values, 0.25)
        q3 = percentile(values, 0.75)
        p5 = percentile(values, 0.05)
        p95 = percentile(values, 0.95)
        color = _REGIME_COLORS[regime]
        title, subtitle = _REGIME_LABELS[regime]
        body.extend(
            [
                f'<rect x="{panel_left:.1f}" y="96" width="{panel_width:.1f}" height="420" fill="#FAFBFC" stroke="#E5E9EC"/>',
                f'<text x="{panel_left + panel_width / 2:.1f}" y="120" text-anchor="middle" font-family="{_SVG_FONT}" font-size="15" font-weight="700" fill="{_SVG_TEXT}">{title}</text>',
                f'<text x="{panel_left + panel_width / 2:.1f}" y="138" text-anchor="middle" font-family="{_SVG_FONT}" font-size="10.5" fill="{_SVG_MUTED}">{subtitle}</text>',
            ]
        )
        for tick_index in range(5):
            tick = lower + scale_span * tick_index / 4
            y = y_position(tick)
            body.extend(
                [
                    f'<line x1="{plot_left:.1f}" y1="{y:.1f}" x2="{plot_right:.1f}" y2="{y:.1f}" stroke="{_SVG_GRID}" stroke-width="1"/>',
                    f'<text x="{plot_left - 8:.1f}" y="{y + 4:.1f}" text-anchor="end" font-family="{_SVG_FONT}" font-size="10" fill="{_SVG_MUTED}">{_format_axis_ms(tick, scale_span / 4)}</text>',
                ]
            )
        body.append(
            f'<text x="{panel_left + 13:.1f}" y="{(plot_top + plot_bottom) / 2:.1f}" transform="rotate(-90 {panel_left + 13:.1f} {(plot_top + plot_bottom) / 2:.1f})" text-anchor="middle" font-family="{_SVG_FONT}" font-size="10" fill="{_SVG_TEXT}">Latency (ms)</text>'
        )
        for point, value in enumerate(values):
            jitter = (((point * 47) % 101) - 50) * 0.9
            body.append(
                f'<circle cx="{center + jitter:.1f}" cy="{y_position(value):.1f}" r="3.5" fill="{color}" fill-opacity="0.50"/>'
            )
        if len(values) > 1:
            box_left = center - 45
            box_top = y_position(q3)
            box_bottom = y_position(q1)
            body.extend(
                [
                    f'<line x1="{center:.1f}" y1="{y_position(p95):.1f}" x2="{center:.1f}" y2="{y_position(p5):.1f}" stroke="{color}" stroke-width="2"/>',
                    f'<line x1="{center - 22:.1f}" y1="{y_position(p95):.1f}" x2="{center + 22:.1f}" y2="{y_position(p95):.1f}" stroke="{color}" stroke-width="2"/>',
                    f'<line x1="{center - 22:.1f}" y1="{y_position(p5):.1f}" x2="{center + 22:.1f}" y2="{y_position(p5):.1f}" stroke="{color}" stroke-width="2"/>',
                    f'<rect x="{box_left:.1f}" y="{box_top:.1f}" width="90" height="{max(box_bottom - box_top, 2):.1f}" fill="#FFFFFF" fill-opacity="0.88" stroke="{color}" stroke-width="2"/>',
                    f'<line x1="{box_left:.1f}" y1="{y_position(median):.1f}" x2="{box_left + 90:.1f}" y2="{y_position(median):.1f}" stroke="{color}" stroke-width="3"/>',
                ]
            )
        spread_percentage = observed_span * 100.0 / median if median else 0.0
        body.extend(
            [
                f'<text x="{panel_left + panel_width / 2:.1f}" y="458" text-anchor="middle" font-family="{_SVG_FONT}" font-size="13" font-weight="700" fill="{color}">Median: {_format_ms(median)} ms</text>',
                f'<text x="{panel_left + panel_width / 2:.1f}" y="480" text-anchor="middle" font-family="{_SVG_FONT}" font-size="11" fill="{_SVG_TEXT}">Observed: {_format_ms(observed_min)} to {_format_ms(observed_max)} ms</text>',
                (
                    f'<text x="{panel_left + panel_width / 2:.1f}" y="500" text-anchor="middle" font-family="{_SVG_FONT}" font-size="10.5" fill="{_SVG_MUTED}">Spread: {_format_ms(observed_span)} ms ({spread_percentage:.3f}%) | {len(values)} observations</text>'
                    if len(values) > 1
                    else f'<text x="{panel_left + panel_width / 2:.1f}" y="500" text-anchor="middle" font-family="{_SVG_FONT}" font-size="10.5" fill="{_SVG_MUTED}">Only one observation; no distribution can be estimated</text>'
                ),
            ]
        )
    body.extend(
        [
            f'<text x="40" y="550" font-family="{_SVG_FONT}" font-size="11" fill="{_SVG_TEXT}">How to read: dots are runs; boxes cover the middle 50%; whiskers cover p5-p95; the dark line is the median.</text>',
            f'<text x="40" y="574" font-family="{_SVG_FONT}" font-size="11" font-weight="700" fill="{_SVG_MUTED}">Panels use different linear scales. Compare printed medians for speed and each panel&apos;s spread for repeatability.</text>',
            "</svg>",
        ]
    )
    path.write_text("\n".join(body) + "\n", encoding="utf-8")


def _phase_svg(path: Path, phases: dict[str, dict[str, Any]]) -> None:
    width, height = 1120, 610
    left, right = 300.0, 960.0
    bar_width = right - left
    regimes = [name for name in ("cold", "warm", "steady") if name in phases]
    selected = tuple(_PHASE_COLORS)
    totals_ms = {
        regime: sum(phases[regime]["aggregates_mean_ns"].values()) / 1e6
        for regime in regimes
    }
    ticks = _linear_ticks(max(totals_ms.values()))
    axis_maximum = ticks[-1]
    body = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description">',
        '<title id="title">Where does the measured time go?</title>',
        '<desc id="description">Absolute-time stacked bars compare deployment and repeated-inference latency while colours identify runtime phases.</desc>',
        '<rect width="100%" height="100%" fill="#FFFFFF"/>',
        f'<text x="40" y="42" font-family="{_SVG_FONT}" font-size="24" font-weight="700" fill="{_SVG_TEXT}">Where does the measured time go?</text>',
        f'<text x="40" y="70" font-family="{_SVG_FONT}" font-size="13" fill="{_SVG_MUTED}">Bar length is proportional to mean elapsed time; colour shows how that time is spent.</text>',
    ]
    for tick in ticks:
        x = left + bar_width * tick / axis_maximum
        body.extend(
            [
                f'<line x1="{x:.1f}" y1="108" x2="{x:.1f}" y2="395" stroke="{_SVG_GRID}" stroke-width="1"/>',
                f'<text x="{x:.1f}" y="100" text-anchor="middle" font-family="{_SVG_FONT}" font-size="11" fill="{_SVG_MUTED}">{_format_axis_ms(tick, ticks[1] - ticks[0])}</text>',
            ]
        )
    for index, regime in enumerate(regimes):
        values = phases[regime]["aggregates_mean_ns"]
        total_ms = totals_ms[regime]
        y = 140.0 + index * 92
        title, subtitle = _REGIME_LABELS[regime]
        body.extend(
            [
                f'<text x="{left - 20:.0f}" y="{y + 17:.1f}" text-anchor="end" font-family="{_SVG_FONT}" font-size="14" font-weight="700" fill="{_SVG_TEXT}">{title}</text>',
                f'<text x="{left - 20:.0f}" y="{y + 36:.1f}" text-anchor="end" font-family="{_SVG_FONT}" font-size="10.5" fill="{_SVG_MUTED}">{subtitle}</text>',
            ]
        )
        x = left
        for name in selected:
            value_ms = values.get(name, 0.0) / 1e6
            segment = bar_width * value_ms / axis_maximum
            if segment <= 0:
                continue
            if segment >= 0.5:
                body.append(
                    f'<rect x="{x:.1f}" y="{y:.1f}" width="{segment:.1f}" height="48" fill="{_PHASE_COLORS[name]}"/>'
                )
                if segment >= 62:
                    text_color = _SVG_TEXT if name in (
                        "result_handling",
                        "unprofiled_process_and_launch",
                    ) else "#FFFFFF"
                    body.append(
                        f'<text x="{x + segment / 2:.1f}" y="{y + 29:.1f}" text-anchor="middle" font-family="{_SVG_FONT}" font-size="10.5" font-weight="700" fill="{text_color}">{_format_ms(value_ms)} ms</text>'
                    )
            x += segment
        body.append(
            f'<text x="{x + 12:.1f}" y="{y + 29:.1f}" font-family="{_SVG_FONT}" font-size="12" font-weight="700" fill="{_SVG_TEXT}">{_format_ms(total_ms)} ms total</text>'
        )
    body.append(
        f'<text x="{(left + right) / 2:.1f}" y="425" text-anchor="middle" font-family="{_SVG_FONT}" font-size="12" fill="{_SVG_TEXT}">Mean elapsed time (milliseconds)</text>'
    )
    legend_y = 475
    for index, name in enumerate(selected):
        column = index % 4
        row = index // 4
        x = 48 + column * 268
        y = legend_y + row * 36
        body.extend(
            [
                f'<rect x="{x}" y="{y - 13}" width="16" height="16" fill="{_PHASE_COLORS[name]}"/>',
                f'<text x="{x + 24}" y="{y}" font-family="{_SVG_FONT}" font-size="11" fill="{_SVG_TEXT}">{_PHASE_LABELS[name]}</text>',
            ]
        )
    body.extend(
        [
            f'<text x="40" y="570" font-family="{_SVG_FONT}" font-size="11" fill="{_SVG_MUTED}">Deployment bars include process startup and shutdown. Repeated inference measures execution with the model already loaded.</text>',
            "</svg>",
        ]
    )
    path.write_text("\n".join(body) + "\n", encoding="utf-8")


def _throughput_svg(path: Path, throughput: dict[str, Any]) -> None:
    definitions = (
        (
            "Cold deployment",
            "cold_deployment_images_per_second",
            _REGIME_COLORS["cold"],
            False,
        ),
        (
            "Warm deployment",
            "warm_end_to_end_images_per_second",
            _REGIME_COLORS["warm"],
            False,
        ),
        (
            "Repeated inference",
            "steady_runtime_execution_images_per_second",
            _REGIME_COLORS["steady"],
            False,
        ),
        (
            "Calculated pipeline ceiling",
            "theoretical_stage_bottleneck_upper_bound_images_per_second",
            "#6F4E7C",
            True,
        ),
    )
    rows = [
        (label, float(throughput[key]), color, theoretical)
        for label, key, color, theoretical in definitions
        if key in throughput and float(throughput[key]) > 0
    ]
    values = [row[1] for row in rows]
    lower = max(min(values) * 0.65, 1e-6)
    upper = max(max(values) * 1.55, lower * 2)
    min_log = math.log10(lower)
    span = max(math.log10(upper) - min_log, 0.4)
    width, height = 1100, 500
    left, right, top = 275.0, 1000.0, 120.0

    def x_position(value: float) -> float:
        return left + (math.log10(value) - min_log) / span * (right - left)

    body = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description">',
        '<title id="title">How many inferences fit into one second?</title>',
        '<desc id="description">Measured deployment and repeated-inference throughput are compared with a separately marked calculated pipeline ceiling.</desc>',
        '<rect width="100%" height="100%" fill="#FFFFFF"/>',
        f'<text x="40" y="42" font-family="{_SVG_FONT}" font-size="24" font-weight="700" fill="{_SVG_TEXT}">How many inferences fit into one second?</text>',
        f'<text x="40" y="70" font-family="{_SVG_FONT}" font-size="13" fill="{_SVG_MUTED}">Higher is faster. Deployment includes model setup; repeated inference keeps the model loaded.</text>',
    ]
    for tick in _log_ticks(lower, upper):
        x = x_position(tick)
        body.extend(
            [
                f'<line x1="{x:.1f}" y1="{top - 18:.0f}" x2="{x:.1f}" y2="408" stroke="{_SVG_GRID}" stroke-width="1"/>',
                f'<text x="{x:.1f}" y="430" text-anchor="middle" font-family="{_SVG_FONT}" font-size="11" fill="{_SVG_MUTED}">{_format_rate(tick)}</text>',
            ]
        )
    for index, (label, value, color, theoretical) in enumerate(rows):
        y = top + index * 72
        x = x_position(value)
        body.extend(
            [
                f'<text x="{left - 20:.0f}" y="{y + 5:.1f}" text-anchor="end" font-family="{_SVG_FONT}" font-size="14" font-weight="700" fill="{_SVG_TEXT}">{label}</text>',
                f'<line x1="{left:.1f}" y1="{y:.1f}" x2="{right:.1f}" y2="{y:.1f}" stroke="#EEF1F3" stroke-width="3"/>',
            ]
        )
        if theoretical:
            body.append(
                f'<rect x="{x - 7:.1f}" y="{y - 7:.1f}" width="14" height="14" transform="rotate(45 {x:.1f} {y:.1f})" fill="#FFFFFF" stroke="{color}" stroke-width="3"/>'
            )
        else:
            body.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="8" fill="{color}" stroke="#FFFFFF" stroke-width="2"/>'
            )
        anchor = "end" if x > right - 90 else "start"
        label_x = x - 14 if anchor == "end" else x + 14
        body.append(
            f'<text x="{label_x:.1f}" y="{y - 13:.1f}" text-anchor="{anchor}" font-family="{_SVG_FONT}" font-size="12" font-weight="700" fill="{color}">{_format_rate(value)} images/s</text>'
        )
    body.extend(
        [
            f'<text x="{(left + right) / 2:.0f}" y="463" text-anchor="middle" font-family="{_SVG_FONT}" font-size="12" fill="{_SVG_TEXT}">Throughput (images/s, logarithmic scale)</text>',
            f'<text x="40" y="486" font-family="{_SVG_FONT}" font-size="10.5" fill="{_SVG_MUTED}">The outlined diamond is a calculated ceiling, not throughput demonstrated by the current blocking runtime.</text>',
            "</svg>",
        ]
    )
    path.write_text("\n".join(body) + "\n", encoding="utf-8")


def _session_variability_svg(path: Path, summaries: dict[str, Any]) -> None:
    regimes = [name for name in ("cold", "warm", "steady") if name in summaries]
    session_names = sorted(
        {
            session
            for regime in regimes
            for session in summaries[regime]["sessions"]
        }
    )
    width, height = 1120, 440
    if len(session_names) < 2:
        body = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="270" viewBox="0 0 {width} 270" role="img" aria-labelledby="title description">',
            '<title id="title">Can results be reproduced after a fresh boot?</title>',
            '<desc id="description">Fresh-boot variability cannot be estimated because this report contains only one independent boot session.</desc>',
            '<rect width="100%" height="100%" fill="#FFFFFF"/>',
            f'<text x="40" y="42" font-family="{_SVG_FONT}" font-size="24" font-weight="700" fill="{_SVG_TEXT}">Can results be reproduced after a fresh boot?</text>',
            f'<rect x="40" y="88" width="1040" height="118" fill="#FAFBFC" stroke="#D9DEE3"/>',
            f'<text x="560" y="128" text-anchor="middle" font-family="{_SVG_FONT}" font-size="18" font-weight="700" fill="{_SVG_TEXT}">Not enough independent boots to measure variability</text>',
            f'<text x="560" y="158" text-anchor="middle" font-family="{_SVG_FONT}" font-size="13" fill="{_SVG_MUTED}">This report contains 1 fresh-boot session. One result has no between-boot spread.</text>',
            f'<text x="560" y="182" text-anchor="middle" font-family="{_SVG_FONT}" font-size="13" fill="{_SVG_MUTED}">Use at least 2 sessions to estimate variability; the final campaign targets 5.</text>',
            f'<text x="40" y="242" font-family="{_SVG_FONT}" font-size="11" fill="{_SVG_MUTED}">Within-session repeatability is shown in the latency distribution figure instead.</text>',
            "</svg>",
        ]
        path.write_text("\n".join(body) + "\n", encoding="utf-8")
        return

    variability: dict[str, dict[str, Any]] = {}
    maximum_deviation = 0.0
    for regime in regimes:
        medians = {
            session: summaries[regime]["sessions"][session]["median_ns"] / 1e6
            for session in summaries[regime]["sessions"]
        }
        reference = statistics.median(medians.values())
        deviations = {
            session: (value / reference - 1.0) * 100.0
            for session, value in medians.items()
        }
        ci = summaries[regime]["session_median_bootstrap_95ci"]
        ci_low = (ci["lower_ns"] / 1e6 / reference - 1.0) * 100.0
        ci_high = (ci["upper_ns"] / 1e6 / reference - 1.0) * 100.0
        variability[regime] = {
            "reference": reference,
            "deviations": deviations,
            "ci_low": ci_low,
            "ci_high": ci_high,
        }
        maximum_deviation = max(
            maximum_deviation,
            *(abs(value) for value in deviations.values()),
            abs(ci_low),
            abs(ci_high),
        )
    limit = max(1.0, math.ceil(maximum_deviation * 1.25 * 10.0) / 10.0)
    left, right = 300.0, 1040.0

    def x_position(value: float) -> float:
        return left + (value + limit) / (2 * limit) * (right - left)

    body = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description">',
        '<title id="title">Can results be reproduced after a fresh boot?</title>',
        '<desc id="description">Each dot is one boot session median shown as a percentage difference from the cross-session median.</desc>',
        '<rect width="100%" height="100%" fill="#FFFFFF"/>',
        f'<text x="40" y="42" font-family="{_SVG_FONT}" font-size="24" font-weight="700" fill="{_SVG_TEXT}">Can results be reproduced after a fresh boot?</text>',
        f'<text x="40" y="70" font-family="{_SVG_FONT}" font-size="13" fill="{_SVG_MUTED}">Each dot is one boot&apos;s median. Distance from 0% shows how much that boot differs from the typical boot.</text>',
    ]
    tick_values = (-limit, -limit / 2, 0.0, limit / 2, limit)
    for tick in tick_values:
        x = x_position(tick)
        body.extend(
            [
                f'<line x1="{x:.1f}" y1="104" x2="{x:.1f}" y2="355" stroke="{"#8B959E" if tick == 0 else _SVG_GRID}" stroke-width="{"2" if tick == 0 else "1"}"/>',
                f'<text x="{x:.1f}" y="96" text-anchor="middle" font-family="{_SVG_FONT}" font-size="10.5" fill="{_SVG_MUTED}">{tick:+.2f}%</text>',
            ]
        )
    for index, regime in enumerate(regimes):
        y = 145.0 + index * 92
        title, subtitle = _REGIME_LABELS[regime]
        data = variability[regime]
        body.extend(
            [
                f'<text x="{left - 20:.1f}" y="{y - 2:.1f}" text-anchor="end" font-family="{_SVG_FONT}" font-size="14" font-weight="700" fill="{_SVG_TEXT}">{title}</text>',
                f'<text x="{left - 20:.1f}" y="{y + 17:.1f}" text-anchor="end" font-family="{_SVG_FONT}" font-size="10.5" fill="{_SVG_MUTED}">{subtitle}</text>',
                f'<line x1="{x_position(data["ci_low"]):.1f}" y1="{y:.1f}" x2="{x_position(data["ci_high"]):.1f}" y2="{y:.1f}" stroke="{_REGIME_COLORS[regime]}" stroke-width="8" stroke-linecap="round" stroke-opacity="0.32"/>',
            ]
        )
        for session_index, session in enumerate(session_names):
            if session in data["deviations"]:
                point_y = y + (((session_index * 11) % 5) - 2) * 4
                deviation = data["deviations"][session]
                body.append(
                    f'<circle cx="{x_position(deviation):.1f}" cy="{point_y:.1f}" r="6" fill="{_REGIME_COLORS[regime]}" stroke="#FFFFFF" stroke-width="1.5"><title>Session {session_index + 1}: {deviation:+.4f}%</title></circle>'
                )
        body.append(
            f'<text x="{right:.1f}" y="{y + 25:.1f}" text-anchor="end" font-family="{_SVG_FONT}" font-size="10.5" fill="{_SVG_MUTED}">typical median: {_format_ms(data["reference"])} ms</text>'
        )
    body.extend(
        [
            f'<text x="{(left + right) / 2:.1f}" y="382" text-anchor="middle" font-family="{_SVG_FONT}" font-size="12" fill="{_SVG_TEXT}">Difference from the cross-boot median (lower is faster)</text>',
            f'<text x="40" y="418" font-family="{_SVG_FONT}" font-size="11" fill="{_SVG_MUTED}">Dots show independent boots; translucent bars show the bootstrap 95% confidence interval across boot medians.</text>',
            "</svg>",
        ]
    )
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
    boot_ids: set[str] = set()

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
            environment = _environment_evidence(root, env)
            if environment["boot_id"] in boot_ids:
                raise ValueError(
                    f"{archive}: duplicate Linux boot ID; sessions must come from "
                    "independent fresh boots"
                )
            boot_ids.add(environment["boot_id"])
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
                    "environment": environment,
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
        "schema_version": 3,
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
                "the Linux boot ID is unique across imported sessions",
                "wall-clock synchronization status is recorded",
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
    power_rows: list[dict[str, Any]] = []
    for session in sessions:
        power_result = session["power"]
        if power_result.get("status") != "available":
            continue
        for scope_type in ("domains", "rails"):
            for scope, metrics in power_result[scope_type].items():
                power_rows.append(
                    {
                        "session": session["session"],
                        "power_phase": power_result["power_phase"],
                        "scope_type": scope_type.removesuffix("s"),
                        "scope": scope,
                        "executed_iterations": power_result["executed_iterations"],
                        **metrics,
                    }
                )
    if power_rows:
        _write_csv(out_dir / "power-summary.csv", power_rows)
    _latency_svg(out_dir / "latency-distribution.svg", grouped)
    _phase_svg(
        out_dir / "phase-breakdown.svg",
        {regime: result["phases"] for regime, result in summaries.items()},
    )
    _throughput_svg(
        out_dir / "throughput-comparison.svg",
        summary["throughput_definitions"],
    )
    _session_variability_svg(
        out_dir / "session-variability.svg",
        summaries,
    )
    if len(sessions) < 2:
        session_figure_explanation = [
            "This report contains only one independent fresh-boot session, so "
            "between-boot variability cannot be estimated. The figure records this "
            "limitation instead of presenting a meaningless spread.",
        ]
    else:
        session_figure_explanation = [
            "Each dot is one fresh boot's median expressed as a percentage difference "
            "from the cross-boot median. Values close to zero indicate reproducible "
            "performance after rebooting.",
        ]

    if any(
        session["environment"]["temperature_before_status"] == "available"
        or session["environment"]["temperature_after_status"] == "available"
        for session in sessions
    ):
        temperature_summary = (
            "available; before/after sensor counts by session: "
            + ", ".join(
                f"{session['environment']['temperature_before_sensor_count']}/"
                f"{session['environment']['temperature_after_sensor_count']}"
                for session in sessions
            )
        )
    else:
        temperature_summary = "unavailable on this image (recorded explicitly)"

    report = [
        f"# NVDLA {baseline['model']} Performance Report",
        "",
        f"- Independent fresh-boot sessions: {len(sessions)}",
        "- Session identity: unique Linux boot IDs",
        f"- Wall-clock status: NTP synchronized for "
        f"{sum(session['environment']['ntp_synchronized'] == 'yes' for session in sessions)}/"
        f"{len(sessions)} sessions (not used for latency timing or acceptance)",
        f"- Temperature evidence: {temperature_summary}",
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

    available_power = [
        session for session in sessions if session["power"].get("status") == "available"
    ]
    if available_power:
        report.extend(
            [
                "",
                "## Power And Energy",
                "",
                "Power is sampled concurrently with the correctness-checked steady "
                "profile. PS includes software-stack and processing-system activity; "
                "PL includes the FPGA fabric. Incremental values are active minus the "
                "driver-loaded idle baseline and remain signed rather than being "
                "clamped to zero.",
                "",
                "| Session | Domain | Idle (W) | Active (W) | Incremental (W) | "
                "Active energy/inference (mJ) | Incremental energy/inference (mJ) |",
                "|---|---|---:|---:|---:|---:|---:|",
            ]
        )
        for session in available_power:
            for domain in ("PS", "PL", "MONITORED"):
                metrics = session["power"]["domains"].get(domain)
                if not metrics:
                    continue
                report.append(
                    f"| {session['session']} | {domain} | "
                    f"{metrics['idle_mean_watts']:.6f} | "
                    f"{metrics['active_mean_watts']:.6f} | "
                    f"{metrics['incremental_mean_watts']:.6f} | "
                    f"{metrics['active_energy_joules_per_inference'] * 1000:.6f} | "
                    f"{metrics['incremental_energy_joules_per_inference'] * 1000:.6f} |"
                )
        report.extend(
            [
                "",
                "The monitored total is the sum of exposed rails, not 12 V board-input "
                "power. Per-rail results and raw sample counts are retained in "
                "`power-summary.csv` and `performance-summary.json`.",
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
            "Each timing regime has its own linear scale so its run-to-run spread is "
            "visible. Compare the printed medians for speed; compare the dots, box, "
            "and observed range within each panel for repeatability.",
            "",
            "![Phase breakdown](phase-breakdown.svg)",
            "",
            "Bar length represents absolute mean elapsed time on one shared scale. "
            "The coloured segments show which runtime phases account for that time.",
            "",
            "![Throughput comparison](throughput-comparison.svg)",
            "",
            "Higher throughput is better. Deployment rates include setup; repeated "
            "inference keeps the model resident. The outlined value is calculated, "
            "not measured pipelined throughput.",
            "",
            "![Fresh-boot session variability](session-variability.svg)",
            "",
            *session_figure_explanation,
            "",
            "## Timing Boundaries",
            "",
            "Cold and warm results use the external launch-to-exit clock. Steady-state "
            "results use runtime execution latency (blocking `IRuntime::submit()`), "
            "which includes UMD submission, "
            "the KMD ioctl and scheduler, hardware execution, and IRQ completion.",
            "",
            "Power results, when available, are sampled concurrently with the measured "
            "steady run and integrated over its exact launcher start/end interval.",
        ]
    )
    (out_dir / "performance-report.md").write_text(
        "\n".join(report) + "\n", encoding="utf-8"
    )
    print(f"Performance report: {out_dir / 'performance-report.md'}")
    return 0
