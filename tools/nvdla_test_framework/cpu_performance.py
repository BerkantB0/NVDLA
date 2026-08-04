from __future__ import annotations

import csv
import json
import re
import statistics
import sys
import tarfile
import tempfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from .common import write_json
from .performance import _latency_svg, bootstrap_session_medians, summarize_values


def _parse_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def _safe_extract(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:*") as bundle:
        for member in bundle.getmembers():
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or member.issym() or member.islnk():
                raise ValueError(f"unsafe archive member: {member.name}")
        if sys.version_info >= (3, 12):
            bundle.extractall(destination, filter="data")
        else:
            bundle.extractall(destination)


def _hash_records(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = line.split(maxsplit=1)
        if len(fields) == 2:
            result[Path(fields[1].lstrip("*")).name] = fields[0].lower()
    return result


def _sysfs_snapshot(path: Path) -> list[str]:
    values: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line:
            _, value = line.split("=", 1)
            values.append(value.strip())
    if not values:
        raise ValueError(f"{path}: empty sysfs snapshot")
    return values


def _perf_rows(path: Path) -> list[float]:
    result: list[float] = []
    with path.open(encoding="utf-8", errors="replace", newline="") as stream:
        for row in csv.reader(stream):
            if len(row) != 5:
                continue
            try:
                seconds = float(row[1])
            except ValueError:
                continue
            if seconds <= 0:
                raise ValueError(f"{path}: non-positive inference latency")
            result.append(seconds * 1_000_000_000.0)
    return result


def _stdout_phase(path: Path, pattern: str, multiplier: float) -> float | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(pattern, text)
    return float(match.group(1)) * multiplier if match else None


def _power_summary(root: Path, steady_samples: int) -> dict[str, Any]:
    directory = root / "power-sampling"
    if not directory.is_dir():
        return {"status": "unavailable"}

    def read(path: Path) -> dict[str, list[tuple[int, float]]]:
        series: dict[str, list[tuple[int, float]]] = defaultdict(list)
        sweeps: dict[int, dict[str, Any]] = {}
        with path.open(encoding="utf-8", errors="replace", newline="") as stream:
            reader = csv.DictReader(line for line in stream if not line.startswith("#"))
            for row in reader:
                sample = int(row["sample_index"])
                sweep = sweeps.setdefault(sample, {"timestamp": int(row["timestamp_ns"]), "domains": {}})
                domain = row["domain"]
                sweep["domains"][domain] = sweep["domains"].get(domain, 0.0) + int(row["power_uw"]) / 1_000_000.0
        for sample in sorted(sweeps):
            sweep = sweeps[sample]
            for domain, watts in sweep["domains"].items():
                series[domain].append((sweep["timestamp"], watts))
            series["MONITORED"].append((sweep["timestamp"], sum(sweep["domains"].values())))
        return dict(series)

    def interpolate(values: list[tuple[int, float]], timestamp: int) -> float:
        if timestamp == values[-1][0]:
            return values[-1][1]
        for left, right in zip(values, values[1:]):
            if timestamp == left[0]:
                return left[1]
            if left[0] < timestamp < right[0]:
                fraction = (timestamp - left[0]) / (right[0] - left[0])
                return left[1] + fraction * (right[1] - left[1])
        raise ValueError("power trace does not bracket the benchmark interval")

    def integrate(values: list[tuple[int, float]]) -> float:
        return sum(
            (left[1] + right[1]) * 0.5 * (right[0] - left[0]) / 1_000_000_000.0
            for left, right in zip(values, values[1:])
        )

    idle = read(directory / "idle-readings.csv")
    active = read(directory / "readings.csv")
    if set(idle) != set(active) or not {"PS", "PL", "MONITORED"}.issubset(active):
        raise ValueError("power evidence has inconsistent or incomplete PS/PL domains")
    interval = _parse_env(root / "steady-1" / "launch-interval.env")
    start = int(interval.get("start_ns", "0"))
    end = int(interval.get("end_ns", "0"))
    if start <= 0 or end <= start:
        raise ValueError("invalid steady launcher interval for power integration")

    domains: dict[str, Any] = {}
    for domain in ("PS", "PL", "MONITORED"):
        idle_values = idle[domain]
        active_values = active[domain]
        if len(idle_values) < 2 or len(active_values) < 2:
            raise ValueError(f"insufficient {domain} power samples")
        clipped = [(start, interpolate(active_values, start))]
        clipped.extend(item for item in active_values if start < item[0] < end)
        clipped.append((end, interpolate(active_values, end)))
        idle_seconds = (idle_values[-1][0] - idle_values[0][0]) / 1_000_000_000.0
        active_seconds = (end - start) / 1_000_000_000.0
        idle_energy = integrate(idle_values)
        active_energy = integrate(clipped)
        idle_watts = idle_energy / idle_seconds
        active_watts = active_energy / active_seconds
        dynamic_energy = active_energy - idle_watts * active_seconds
        domains[domain] = {
            "idle_mean_watts": idle_watts,
            "active_mean_watts": active_watts,
            "incremental_mean_watts": active_watts - idle_watts,
            "active_energy_joules": active_energy,
            "incremental_energy_joules": dynamic_energy,
            "incremental_energy_per_executed_inference_joules": dynamic_energy
            / (steady_samples + 1),
        }
    return {
        "status": "available",
        "scope": "steady process launch through exit; includes session creation and one built-in warm-up",
        "measured_inferences": steady_samples,
        "executed_inferences": steady_samples + 1,
        "domains": domains,
    }


def _load_session(archive: Path, destination: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _safe_extract(archive, destination)
    matches = list(destination.rglob("benchmark.env"))
    if len(matches) != 1:
        raise ValueError(f"{archive}: expected exactly one benchmark.env")
    root = matches[0].parent
    env = _parse_env(matches[0])
    if env.get("status") != "0" or env.get("classification") != "correctness-qualified-performance-pass":
        raise ValueError(f"{archive}: benchmark did not pass")
    if not env.get("boot_id"):
        raise ValueError(f"{archive}: missing fresh-boot identity")
    time_sync = _parse_env(root / "time-sync.env")
    if (
        time_sync.get("boot_id") != env.get("boot_id")
        or time_sync.get("ntp_synchronized") != env.get("ntp_synchronized")
    ):
        raise ValueError(f"{archive}: inconsistent time-status evidence")
    for phase in ("before", "after"):
        if (root / f"correctness-{phase}.status").read_text().strip() != "pass":
            raise ValueError(f"{archive}: {phase} correctness check did not pass")
    if (root / "bad-kernel-patterns.txt").read_text().strip():
        raise ValueError(f"{archive}: kernel error patterns were recorded")

    hashes = _hash_records(root / "software-hashes.txt")
    governors_before = _sysfs_snapshot(root / "governors-before.txt")
    governors_after = _sysfs_snapshot(root / "governors-after.txt")
    frequencies_before = _sysfs_snapshot(root / "frequencies-before.txt")
    frequencies_after = _sysfs_snapshot(root / "frequencies-after.txt")
    if set(governors_before + governors_after) != {"userspace"}:
        raise ValueError(f"{archive}: CPU governor was not consistently userspace")
    if len(set(frequencies_before + frequencies_after)) != 1:
        raise ValueError(f"{archive}: CPU frequency was not fixed across the session")
    observed_frequency_khz = int(frequencies_before[0])
    if env.get("cpu_governor") != "userspace":
        raise ValueError(f"{archive}: benchmark governor metadata is inconsistent")
    if int(env.get("cpu_frequency_khz", "0")) != observed_frequency_khz:
        raise ValueError(f"{archive}: benchmark frequency metadata is inconsistent")
    if env.get("cpu_frequency_policy") != "fixed-verified":
        raise ValueError(f"{archive}: CPU frequency policy was not verified")
    workload_manifest = json.loads(
        (root / "cpu-workload-manifest.json").read_text(encoding="utf-8")
    )
    workload = next(
        (
            item
            for item in workload_manifest.get("models", [])
            if item.get("name") == env.get("model")
        ),
        None,
    )
    if not isinstance(workload, dict):
        raise ValueError(f"{archive}: model is absent from the CPU workload manifest")
    variant = workload.get("models", {}).get(env.get("precision"), {})
    test_data = variant.get("test_data", {})
    if (
        str(variant.get("sha256", "")).lower() != hashes.get("model.onnx")
        or str(test_data.get("input", {}).get("sha256", "")).lower()
        != hashes.get("input_0.pb")
        or str(test_data.get("output", {}).get("sha256", "")).lower()
        != hashes.get("output_0.pb")
    ):
        raise ValueError(f"{archive}: workload manifest and measured files differ")
    uname = (root / "uname.txt").read_text(encoding="utf-8", errors="replace").split()
    provenance = {
        "implementation": env.get("implementation"),
        "model": env.get("model"),
        "precision": env.get("precision"),
        "threads": int(env.get("threads", "0")),
        "cpu_affinity_mask": env.get("cpu_affinity_mask"),
        "cpu_governor": env.get("cpu_governor"),
        "cpu_frequency_khz": observed_frequency_khz,
        "cpu_frequency_policy": env.get("cpu_frequency_policy"),
        "kernel_release": uname[2] if len(uname) >= 3 else None,
        "model_sha256": hashes.get("model.onnx"),
        "input_sha256": hashes.get("input_0.pb"),
        "golden_sha256": hashes.get("output_0.pb"),
        "runtime_sha256": hashes.get("onnxruntime_perf_test"),
        "correctness_runner_sha256": hashes.get("onnx_test_runner"),
        "runtime_library_sha256": hashes.get("libonnxruntime.so.1.18.1"),
        "benchmark_runner_sha256": hashes.get("nvdla-board-cpu-benchmark"),
        "launcher_sha256": hashes.get("nvdla-benchmark-launch"),
        "power_sampler_sha256": hashes.get("nvdla-power-sampler"),
        "payload_sha256": hashes.get("SHA256SUMS"),
        "power_sample": env.get("power_sample"),
        "power_interval_ms": env.get("power_interval_ms"),
        "power_sampler_cpu": env.get("power_sampler_cpu"),
        "workload_complexity": {
            "model_size_bytes": variant.get("size_bytes"),
            "node_count": variant.get("node_count"),
            "initializer_count": variant.get("initializer_count"),
            "operator_counts": variant.get("operator_counts"),
            "inputs": variant.get("inputs"),
            "outputs": variant.get("outputs"),
        },
    }
    missing = [
        key
        for key, value in provenance.items()
        if value is None or value == "" or value == 0
    ]
    if missing:
        raise ValueError(f"{archive}: incomplete provenance: {', '.join(missing)}")

    rows: list[dict[str, Any]] = []
    for regime in ("cold", "warm", "steady"):
        for run in sorted(root.glob(f"{regime}-*")):
            samples = _perf_rows(run / "result.csv")
            expected = int(_parse_env(run / "run.env").get("measured_samples", "0"))
            if not samples or len(samples) != expected:
                raise ValueError(f"{run}: measured sample count mismatch")
            launch_ns = int((run / "launch-elapsed-ns.txt").read_text().strip())
            session_create_ns = _stdout_phase(
                run / "runtime.stdout.log", r"Session creation time cost:\s*([0-9.eE+-]+) s", 1e9
            )
            first_inference_ns = _stdout_phase(
                run / "runtime.stdout.log", r"First inference time cost:\s*([0-9.eE+-]+) ms", 1e6
            )
            rusage = _parse_env(run / "rusage.env")
            expected_affinity = f"mask:{env['cpu_affinity_mask']}"
            if rusage.get("cpu_affinity") != expected_affinity:
                raise ValueError(f"{run}: CPU affinity does not match the session")
            for index, inference_ns in enumerate(samples):
                rows.append(
                    {
                        "session": archive.stem,
                        "boot_id": env["boot_id"],
                        "model": env["model"],
                        "precision": env["precision"],
                        "threads": int(env["threads"]),
                        "regime": regime,
                        "run": run.name,
                        "sample_index": index,
                        "latency_ns": launch_ns if regime in {"cold", "warm"} else inference_ns,
                        "inference_ns": inference_ns,
                        "launch_elapsed_ns": launch_ns,
                        "session_create_ns": session_create_ns,
                        "first_inference_ns": first_inference_ns,
                        "cpu_affinity": rusage["cpu_affinity"],
                        "process_user_time_ns": int(rusage.get("user_time_ns", "0")),
                        "process_system_time_ns": int(rusage.get("system_time_ns", "0")),
                        "voluntary_context_switches": int(rusage.get("voluntary_context_switches", "0")),
                        "involuntary_context_switches": int(rusage.get("involuntary_context_switches", "0")),
                    }
                )
    if not rows:
        raise ValueError(f"{archive}: no performance samples")
    session = {
        "archive": str(archive),
        "boot_id": env["boot_id"],
        "timestamp_utc": env.get("timestamp_utc"),
        "ntp_synchronized": env.get("ntp_synchronized"),
        "provenance": provenance,
        "power": _power_summary(root, int(env["steady_samples"]))
        if env.get("power_sample") == "1"
        else {"status": "unavailable"},
    }
    return session, rows


def import_cpu_performance_archives(archives: list[Path], out_dir: Path) -> int:
    try:
        sessions: list[dict[str, Any]] = []
        rows: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index, archive in enumerate(archives):
                session, session_rows = _load_session(archive, root / str(index))
                sessions.append(session)
                rows.extend(session_rows)
        baseline = sessions[0]["provenance"]
        for session in sessions[1:]:
            if session["provenance"] != baseline:
                raise ValueError("refusing to combine CPU sessions with different provenance")
        boot_ids = [session["boot_id"] for session in sessions]
        if len(set(boot_ids)) != len(boot_ids):
            raise ValueError("CPU campaign archives must come from distinct boots")

        summaries: dict[str, Any] = {}
        for regime in ("cold", "warm", "steady"):
            selected = [row for row in rows if row["regime"] == regime]
            if not selected:
                continue
            result = summarize_values(row["latency_ns"] for row in selected)
            medians = [
                statistics.median(
                    row["latency_ns"]
                    for row in selected
                    if row["session"] == Path(session["archive"]).stem
                )
                for session in sessions
                if any(row["session"] == Path(session["archive"]).stem for row in selected)
            ]
            result["session_median_bootstrap_95ci"] = bootstrap_session_medians(medians)
            summaries[regime] = result

        power_sessions = [
            session["power"] for session in sessions if session["power"]["status"] == "available"
        ]
        power_aggregate: dict[str, Any] = {"status": "unavailable"}
        if power_sessions:
            power_aggregate = {
                "status": "available",
                "session_count": len(power_sessions),
                "domains": {
                    domain: {
                        key: statistics.fmean(
                            session["domains"][domain][key] for session in power_sessions
                        )
                        for key in (
                            "idle_mean_watts",
                            "active_mean_watts",
                            "incremental_mean_watts",
                            "incremental_energy_per_executed_inference_joules",
                        )
                    }
                    for domain in ("PS", "PL", "MONITORED")
                },
            }

        out_dir.mkdir(parents=True, exist_ok=True)
        fields = list(rows[0])
        with (out_dir / "cpu-performance-raw.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        summary = {
            "schema_version": 1,
            "status": "pass",
            "provenance": baseline,
            "session_count": len(sessions),
            "sessions": sessions,
            "regimes": summaries,
            "power": power_aggregate,
        }
        write_json(out_dir / "cpu-performance-summary.json", summary)
        grouped = {
            regime: [row for row in rows if row["regime"] == regime]
            for regime in ("cold", "warm", "steady")
        }
        _latency_svg(out_dir / "cpu-latency-distribution.svg", grouped)
        with (out_dir / "cpu-performance-summary.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                [
                    "regime",
                    "samples",
                    "mean_ms",
                    "median_ms",
                    "standard_deviation_ms",
                    "coefficient_of_variation",
                    "p5_ms",
                    "p95_ms",
                    "bootstrap_95ci_lower_ms",
                    "bootstrap_95ci_upper_ms",
                    "images_per_second",
                ]
            )
            for regime, values in summaries.items():
                confidence = values["session_median_bootstrap_95ci"]
                writer.writerow(
                    [
                        regime,
                        values["count"],
                        values["mean_ns"] / 1e6,
                        values["median_ns"] / 1e6,
                        values["standard_deviation_ns"] / 1e6,
                        values["coefficient_of_variation"],
                        values["p5_ns"] / 1e6,
                        values["p95_ns"] / 1e6,
                        confidence["lower_ns"] / 1e6,
                        confidence["upper_ns"] / 1e6,
                        values["throughput_images_per_second"],
                    ]
                )
        lines = [
            "# ARM CPU ONNX Runtime Performance",
            "",
            f"- Model: `{baseline['model']}`",
            f"- Precision: `{baseline['precision']}`",
            f"- CPU threads: `{baseline['threads']}`",
            f"- CPU affinity mask: `{baseline['cpu_affinity_mask']}`",
            f"- CPU operating point: `{baseline['cpu_governor']}` governor, "
            f"fixed at `{baseline['cpu_frequency_khz'] / 1_000_000:.3f} GHz`",
            f"- ONNX graph: `{baseline['workload_complexity']['node_count']}` nodes, "
            f"`{baseline['workload_complexity']['model_size_bytes']}` bytes",
            "- Operators: `"
            + ", ".join(
                f"{name}={count}"
                for name, count in baseline["workload_complexity"]["operator_counts"].items()
            )
            + "`",
            f"- Independent fresh-boot sessions: `{len(sessions)}`",
            f"- Wall-clock synchronization: `"
            f"{sum(session['ntp_synchronized'] == 'yes' for session in sessions)}/"
            f"{len(sessions)}` sessions; not used for latency timing or acceptance",
            "- Correctness: standard `onnx_test_runner` passed before and after every session.",
            "",
            "| Regime | N | Median (ms) | Mean +/- SD (ms) | CV (%) | P5-P95 (ms) | Boot-median 95% CI (ms) | Images/s |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for regime, values in summaries.items():
            confidence = values["session_median_bootstrap_95ci"]
            lines.append(
                f"| {regime} | {values['count']} | {values['median_ns'] / 1e6:.3f} | "
                f"{values['mean_ns'] / 1e6:.3f} +/- {values['standard_deviation_ns'] / 1e6:.3f} | "
                f"{values['coefficient_of_variation'] * 100:.3f} | "
                f"{values['p5_ns'] / 1e6:.3f}-{values['p95_ns'] / 1e6:.3f} | "
                f"{confidence['lower_ns'] / 1e6:.3f}-{confidence['upper_ns'] / 1e6:.3f} | "
                f"{values['throughput_images_per_second']:.3f} |"
            )
        if power_aggregate["status"] == "available":
            lines.extend(
                [
                    "",
                    "| Power domain | Idle mean (W) | Active mean (W) | Increment (W) | Incremental energy/inference (mJ) |",
                    "|---|---:|---:|---:|---:|",
                ]
            )
            for domain, values in power_aggregate["domains"].items():
                lines.append(
                    f"| {domain} | {values['idle_mean_watts']:.4f} | "
                    f"{values['active_mean_watts']:.4f} | "
                    f"{values['incremental_mean_watts']:.4f} | "
                    f"{values['incremental_energy_per_executed_inference_joules'] * 1000:.4f} |"
                )
        lines.extend(
            [
                "",
                "![CPU latency distribution](cpu-latency-distribution.svg)",
                "",
                "Cold and warm values are process launch through exit and include model/session creation, "
                "one standard ORT warm-up inference, and one measured inference. Steady values are "
                "per-inference latency from one loaded session after ORT's built-in warm-up.",
            ]
        )
        (out_dir / "cpu-performance-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"CPU performance report: {out_dir / 'cpu-performance-report.md'}")
    return 0
