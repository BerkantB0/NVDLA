from __future__ import annotations

import html
import math
import statistics
from pathlib import Path
from typing import Any

from .common import read_json, sha256_file, write_json


IMPLEMENTATIONS = ("nvdla", "cpu")
MODELS = ("lenet", "resnet50")
REGIMES = ("cold", "warm", "steady")
COLORS = {"nvdla": "#0b7285", "cpu": "#c2410c"}
LABELS = {"nvdla": "NVDLA FPGA (INT8)", "cpu": "ARM CPU (FP32, 4 threads)"}
MODEL_LABELS = {"lenet": "LeNet", "resnet50": "ResNet-50"}


def _summary_path(reports: Path, implementation: str, model: str, kind: str) -> Path:
    name = "performance-summary.json" if implementation == "nvdla" else "cpu-performance-summary.json"
    return reports / f"{implementation}-{model}-{kind}" / name


def _load_cohort(reports: Path, implementation: str, model: str, kind: str) -> dict[str, Any]:
    path = _summary_path(reports, implementation, model, kind)
    data = read_json(path)
    sessions = data.get("sessions", [])
    if data.get("session_count") != 5 or len(sessions) != 5:
        raise ValueError(f"{path}: final cohort must contain exactly five sessions")
    if data.get("provenance", {}).get("model") != model:
        raise ValueError(f"{path}: model provenance mismatch")
    expected_power = "1" if kind == "power" else "0"
    if str(data.get("provenance", {}).get("power_sample")) != expected_power:
        raise ValueError(f"{path}: expected power_sample={expected_power}")
    boot_ids = [
        session.get("boot_id") or session.get("environment", {}).get("boot_id")
        for session in sessions
    ]
    if None in boot_ids or len(set(boot_ids)) != 5:
        raise ValueError(f"{path}: sessions must have five unique boot IDs")
    if implementation == "nvdla":
        if data.get("correctness_qualification", {}).get("status") != "qualified":
            raise ValueError(f"{path}: NVDLA samples are not correctness-qualified")
    elif data.get("status") != "pass":
        raise ValueError(f"{path}: CPU samples are not correctness-qualified")
    if kind == "power":
        for session in sessions:
            if session.get("power", {}).get("status") != "available":
                raise ValueError(f"{path}: missing power evidence")
    return data


def _latency(cohort: dict[str, Any], implementation: str, regime: str) -> dict[str, float]:
    values = cohort["regimes"][regime]
    latency = values["latency"] if implementation == "nvdla" else values
    confidence = values["session_median_bootstrap_95ci"]
    return {
        "median_ms": latency["median_ns"] / 1_000_000.0,
        "mean_ms": latency["mean_ns"] / 1_000_000.0,
        "p5_ms": latency["p5_ns"] / 1_000_000.0,
        "p95_ms": latency["p95_ns"] / 1_000_000.0,
        "session_median_ms": confidence["estimate_ns"] / 1_000_000.0,
        "ci_lower_ms": confidence["lower_ns"] / 1_000_000.0,
        "ci_upper_ms": confidence["upper_ns"] / 1_000_000.0,
        "samples": latency["count"],
        "outliers_retained": latency["outlier_count"],
    }


def _mean_summary(values: list[float]) -> dict[str, float | int]:
    return {
        "session_count": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "standard_deviation": statistics.stdev(values) if len(values) > 1 else 0.0,
        "minimum": min(values),
        "maximum": max(values),
    }


def _change_percentage(candidate: float, reference: float) -> float:
    return (candidate / reference - 1.0) * 100.0


def _latency_label(milliseconds: float) -> str:
    return f"{milliseconds / 1000.0:.3g} s" if milliseconds >= 1000.0 else f"{milliseconds:.3g} ms"


def _energy_label(millijoules: float) -> str:
    return f"{millijoules / 1000.0:.3g} J" if millijoules >= 1000.0 else f"{millijoules:.3g} mJ"


def _power(cohort: dict[str, Any], implementation: str) -> dict[str, Any]:
    active: list[float] = []
    idle: list[float] = []
    incremental: list[float] = []
    active_energy: list[float] = []
    incremental_energy: list[float] = []
    executed: list[int] = []
    for session in cohort["sessions"]:
        power = session["power"]
        domain = power["domains"]["MONITORED"]
        count_key = "executed_iterations" if implementation == "nvdla" else "executed_inferences"
        count = int(power[count_key])
        active.append(float(domain["active_mean_watts"]))
        idle.append(float(domain["idle_mean_watts"]))
        incremental.append(float(domain["incremental_mean_watts"]))
        if implementation == "nvdla":
            active_energy.append(float(domain["active_energy_joules_per_inference"]))
            incremental_energy.append(float(domain["incremental_energy_joules_per_inference"]))
        else:
            active_energy.append(float(domain["active_energy_joules"]) / count)
            incremental_energy.append(float(domain["incremental_energy_per_executed_inference_joules"]))
        executed.append(count)
    if len(set(executed)) != 1:
        raise ValueError("power sessions have inconsistent executed inference counts")
    return {
        "scope": "sum of monitored PS and PL rails during the steady process interval",
        "executed_inferences_per_session": executed[0],
        "active_watts": _mean_summary(active),
        "idle_watts": _mean_summary(idle),
        "incremental_watts": _mean_summary(incremental),
        "active_joules_per_inference": _mean_summary(active_energy),
        "incremental_joules_per_inference": _mean_summary(incremental_energy),
    }


def _svg_start(title: str, subtitle: str, width: int = 1100, height: int = 650) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<style>text{font-family:Arial,sans-serif;fill:#202124;letter-spacing:0}.title{font-size:25px;font-weight:700}.subtitle{font-size:14px;fill:#59636e}.axis{font-size:13px;fill:#59636e}.label{font-size:14px;font-weight:600}.value{font-size:13px;font-weight:700}.grid{stroke:#d9dee3;stroke-width:1}.baseline{stroke:#69727c;stroke-width:1.5}</style>',
        f'<text x="70" y="42" class="title">{html.escape(title)}</text>',
        f'<text x="70" y="67" class="subtitle">{html.escape(subtitle)}</text>',
    ]


def _legend(lines: list[str], y: int = 92) -> None:
    x = 70
    for implementation in IMPLEMENTATIONS:
        lines.append(f'<rect x="{x}" y="{y - 12}" width="18" height="12" fill="{COLORS[implementation]}"/>')
        lines.append(f'<text x="{x + 25}" y="{y}" class="axis">{LABELS[implementation]}</text>')
        x += 260


def _latency_svg(summary: dict[str, Any]) -> str:
    lines = _svg_start(
        "End-to-end and steady-state latency",
        "Five fresh-boot sessions per point; log scale preserves both small and large latencies.",
    )
    _legend(lines)
    left, right, top, bottom = 100, 1050, 125, 560
    all_values = [
        summary["models"][model][implementation]["latency"][regime]["session_median_ms"]
        for model in MODELS for regime in REGIMES for implementation in IMPLEMENTATIONS
    ]
    lo = 10 ** math.floor(math.log10(min(all_values)))
    hi = 10 ** math.ceil(math.log10(max(all_values)))
    log_lo, log_hi = math.log10(lo), math.log10(hi)
    def y(value: float) -> float:
        return bottom - (math.log10(value) - log_lo) / (log_hi - log_lo) * (bottom - top)
    tick = lo
    while tick <= hi * 1.001:
        yy = y(tick)
        lines.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{right}" y2="{yy:.1f}" class="grid"/>')
        label = f"{tick:g} ms" if tick < 1000 else f"{tick / 1000:g} s"
        lines.append(f'<text x="{left - 12}" y="{yy + 5:.1f}" text-anchor="end" class="axis">{label}</text>')
        tick *= 10
    groups = [(model, regime) for model in MODELS for regime in REGIMES]
    spacing = (right - left) / len(groups)
    for index, (model, regime) in enumerate(groups):
        center = left + spacing * (index + 0.5)
        if index == 3:
            lines.append(f'<line x1="{left + spacing * 3}" y1="{top}" x2="{left + spacing * 3}" y2="{bottom + 45}" stroke="#9aa1a9" stroke-dasharray="4 4"/>')
        for offset, implementation in ((-22, "nvdla"), (22, "cpu")):
            value = summary["models"][model][implementation]["latency"][regime]["session_median_ms"]
            yy = y(value)
            lines.append(f'<circle cx="{center + offset:.1f}" cy="{yy:.1f}" r="8" fill="{COLORS[implementation]}"/>')
            lines.append(f'<text x="{center + offset:.1f}" y="{yy - 13:.1f}" text-anchor="middle" class="value">{_latency_label(value)}</text>')
        lines.append(f'<text x="{center:.1f}" y="{bottom + 24}" text-anchor="middle" class="label">{regime.title()}</text>')
        if regime == "warm":
            lines.append(f'<text x="{center:.1f}" y="{bottom + 46}" text-anchor="middle" class="axis">{MODEL_LABELS[model]}</text>')
    lines.append('</svg>')
    return "\n".join(lines) + "\n"


def _speedup_svg(summary: dict[str, Any]) -> str:
    lines = _svg_start(
        "Relative latency: CPU time divided by NVDLA time",
        "Values above 1x favor NVDLA; values below 1x favor the ARM CPU. Ratios use session-median estimates.",
    )
    left, right, top, bottom = 235, 1030, 125, 565
    log_lo, log_hi = math.log10(0.4), math.log10(12.0)
    def x(value: float) -> float:
        return left + (math.log10(value) - log_lo) / (log_hi - log_lo) * (right - left)
    for tick in (0.5, 1, 2, 5, 10):
        xx = x(tick)
        css = "baseline" if tick == 1 else "grid"
        lines.append(f'<line x1="{xx:.1f}" y1="{top}" x2="{xx:.1f}" y2="{bottom}" class="{css}"/>')
        lines.append(f'<text x="{xx:.1f}" y="{bottom + 26}" text-anchor="middle" class="axis">{tick:g}x</text>')
    rows = [(model, regime) for model in MODELS for regime in REGIMES]
    for index, (model, regime) in enumerate(rows):
        yy = top + 40 + index * 66
        value = summary["models"][model]["comparison"][regime]["cpu_time_divided_by_nvdla_time"]
        xx = x(value)
        lines.append(f'<text x="{left - 18}" y="{yy + 5}" text-anchor="end" class="label">{MODEL_LABELS[model]} {regime}</text>')
        lines.append(f'<line x1="{x(1):.1f}" y1="{yy}" x2="{xx:.1f}" y2="{yy}" stroke="#aeb5bc" stroke-width="3"/>')
        lines.append(f'<circle cx="{xx:.1f}" cy="{yy}" r="9" fill="{COLORS["nvdla"] if value >= 1 else COLORS["cpu"]}"/>')
        anchor = "start" if value < 8 else "end"
        dx = 14 if anchor == "start" else -14
        lines.append(f'<text x="{xx + dx:.1f}" y="{yy + 5}" text-anchor="{anchor}" class="value">{value:.2f}x</text>')
    lines.append('</svg>')
    return "\n".join(lines) + "\n"


def _bar_svg(summary: dict[str, Any], metric: str) -> str:
    energy = metric == "energy"
    title = "Monitored energy per inference" if energy else "Monitored board power during inference"
    subtitle = (
        "Active and idle-baseline-subtracted energy; PS + PL rails, log scale."
        if energy else "Mean power across the monitored PS + PL rails during each steady process interval."
    )
    lines = _svg_start(title, subtitle)
    _legend(lines)
    left, right, top, bottom = 110, 1050, 130, 555
    groups: list[tuple[str, str]] = []
    series: list[tuple[str, str, float]] = []
    for model in MODELS:
        if energy:
            for kind in ("active", "incremental"):
                groups.append((model, kind))
                key = f"{kind}_joules_per_inference"
                for implementation in IMPLEMENTATIONS:
                    series.append((model, kind, summary["models"][model][implementation]["power"][key]["mean"] * 1000.0))
        else:
            groups.append((model, "active"))
            for implementation in IMPLEMENTATIONS:
                series.append((model, "active", summary["models"][model][implementation]["power"]["active_watts"]["mean"]))
    values = [item[2] for item in series if item[2] > 0]
    if energy:
        lo = 10 ** math.floor(math.log10(min(values)))
        hi = 10 ** math.ceil(math.log10(max(values)))
        log_lo, log_hi = math.log10(lo), math.log10(hi)
        def height(value: float) -> float:
            return (math.log10(value) - log_lo) / (log_hi - log_lo) * (bottom - top)
        tick = lo
        while tick <= hi * 1.001:
            yy = bottom - height(tick)
            lines.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{right}" y2="{yy:.1f}" class="grid"/>')
            lines.append(f'<text x="{left - 12}" y="{yy + 5:.1f}" text-anchor="end" class="axis">{tick:g} mJ</text>')
            tick *= 10
    else:
        hi = math.ceil(max(values) * 1.25)
        def height(value: float) -> float:
            return value / hi * (bottom - top)
        for tick in range(0, hi + 1):
            yy = bottom - height(float(tick))
            lines.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{right}" y2="{yy:.1f}" class="grid"/>')
            lines.append(f'<text x="{left - 12}" y="{yy + 5:.1f}" text-anchor="end" class="axis">{tick} W</text>')
    spacing = (right - left) / len(groups)
    for index, (model, kind) in enumerate(groups):
        center = left + spacing * (index + 0.5)
        for offset, implementation in ((-28, "nvdla"), (28, "cpu")):
            key = f"{kind}_joules_per_inference" if energy else "active_watts"
            factor = 1000.0 if energy else 1.0
            value = summary["models"][model][implementation]["power"][key]["mean"] * factor
            hh = height(value)
            lines.append(f'<rect x="{center + offset - 18:.1f}" y="{bottom - hh:.1f}" width="36" height="{hh:.1f}" fill="{COLORS[implementation]}"/>')
            text = _energy_label(value) if energy else f"{value:.3g} W"
            lines.append(f'<text x="{center + offset:.1f}" y="{bottom - hh - 8:.1f}" text-anchor="middle" class="value">{text}</text>')
        lines.append(f'<text x="{center:.1f}" y="{bottom + 24}" text-anchor="middle" class="label">{MODEL_LABELS[model]}</text>')
        if energy:
            lines.append(f'<text x="{center:.1f}" y="{bottom + 45}" text-anchor="middle" class="axis">{kind.title()}</text>')
    lines.append('</svg>')
    return "\n".join(lines) + "\n"


def _write_report(summary: dict[str, Any], out: Path) -> None:
    lines = [
        "# Final NVDLA FPGA and ARM CPU Benchmark Campaign",
        "",
        "## Qualification",
        "",
        "All 40 selected archives passed their implementation-specific correctness checks and provenance checks. "
        "Each cohort contains five independent fresh-boot sessions. The two additional CPU LeNet power archives "
        "are preserved but excluded by the predeclared balanced-cohort rule, not because of their outcomes.",
        "",
        "This is a **system-level deployed implementation comparison**: NVDLA executes an INT8 loadable, while "
        "ONNX Runtime executes FP32 with four ARM Cortex-A53 threads. It is not an equal-precision kernel comparison.",
        "",
        "## Latency",
        "",
        "Primary latency comes only from runs without power sampling. Values below are the median across the five "
        "independent session medians; parentheses show deterministic 95% bootstrap intervals.",
        "",
        "| Model | Regime | NVDLA latency | CPU latency | CPU time / NVDLA time |",
        "|---|---:|---:|---:|---:|",
    ]
    for model in MODELS:
        for regime in REGIMES:
            nvdla = summary["models"][model]["nvdla"]["latency"][regime]
            cpu = summary["models"][model]["cpu"]["latency"][regime]
            ratio = summary["models"][model]["comparison"][regime]["cpu_time_divided_by_nvdla_time"]
            lines.append(
                f"| {MODEL_LABELS[model]} | {regime.title()} | "
                f"{nvdla['session_median_ms']:.3f} ms ({nvdla['ci_lower_ms']:.3f}-{nvdla['ci_upper_ms']:.3f}) | "
                f"{cpu['session_median_ms']:.3f} ms ({cpu['ci_lower_ms']:.3f}-{cpu['ci_upper_ms']:.3f}) | {ratio:.2f}x |"
            )
    lines.extend([
        "",
        "A ratio above 1 means NVDLA is faster. NVDLA reduces ResNet-50 latency in all three regimes. "
        "For LeNet, fixed deployment overhead dominates cold and warm execution, while the CPU is faster in "
        "the steady loaded-context microbenchmark.",
        "",
        "![Latency comparison](latency-comparison.svg)",
        "",
        "![Relative latency](relative-latency.svg)",
        "",
        "## Power And Energy",
        "",
        "Power results use separate correctness-qualified cohorts sampled concurrently with steady execution. "
        "The reported scope is the sum of exposed PS and PL rails, not external 12 V board-input power. "
        "Active energy includes the full sampled steady process interval amortized over all executed inferences; "
        "incremental energy subtracts the measured driver-loaded idle baseline and is therefore secondary.",
        "",
        "| Model | Implementation | Active power | Incremental power | Active energy/inference | Incremental energy/inference |",
        "|---|---|---:|---:|---:|---:|",
    ])
    for model in MODELS:
        for implementation in IMPLEMENTATIONS:
            power = summary["models"][model][implementation]["power"]
            lines.append(
                f"| {MODEL_LABELS[model]} | {LABELS[implementation]} | "
                f"{power['active_watts']['mean']:.3f} W | {power['incremental_watts']['mean']:.3f} W | "
                f"{power['active_joules_per_inference']['mean'] * 1000:.3f} mJ | "
                f"{power['incremental_joules_per_inference']['mean'] * 1000:.3f} mJ |"
            )
    lines.extend([
        "",
        "## Comparative Findings",
        "",
        "| Model | NVDLA active-power change | NVDLA active-energy change | NVDLA incremental-energy change |",
        "|---|---:|---:|---:|",
    ])
    for model in MODELS:
        comparison = summary["models"][model]["power_comparison"]
        lines.append(
            f"| {MODEL_LABELS[model]} | {comparison['active_power_change_percentage']:+.1f}% | "
            f"{comparison['active_energy_change_percentage']:+.1f}% | "
            f"{comparison['incremental_energy_change_percentage']:+.1f}% |"
        )
    lines.extend([
        "",
        "Negative percentages mean NVDLA used less power or energy than the CPU implementation. Active energy is "
        "the conservative primary energy result; incremental energy is useful for isolating workload activity but "
        "depends more strongly on the idle baseline.",
        "",
        "![Monitored power](monitored-power.svg)",
        "",
        "![Monitored energy](monitored-energy.svg)",
        "",
        "## Interpretation Limits",
        "",
        "- No samples or statistical outliers were discarded from the selected cohorts.",
        "- Latency confidence intervals describe variability between five session medians, not uncertainty over arbitrary repeated samples.",
        "- Powered latency is retained in the cohort reports but is not used as the primary latency comparison.",
        "- CPU and NVDLA outputs were independently checked against their established goldens; numeric tensors need not be bit-identical across FP32 and INT8 implementations.",
        "- Monitored power excludes uninstrumented board losses and should not be compared directly with wall-plug measurements.",
        "",
        "## Reproducibility Files",
        "",
        "- `campaign-selection.json`: all included and excluded archive names and hashes.",
        "- `campaign-summary.json`: machine-readable comparison values.",
        "- The eight cohort directories contain raw CSV exports, full statistics, plots, and per-session provenance.",
    ])
    (out / "campaign-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_campaign_report(reports: Path, campaign_root: Path, out: Path) -> int:
    cohorts: dict[tuple[str, str, str], dict[str, Any]] = {}
    for implementation in IMPLEMENTATIONS:
        for model in MODELS:
            for kind in ("latency", "power"):
                cohorts[(implementation, model, kind)] = _load_cohort(reports, implementation, model, kind)

    selected_paths = {
        Path(session["archive"]).resolve()
        for cohort in cohorts.values()
        for session in cohort["sessions"]
    }
    available_paths = {path.resolve() for path in campaign_root.rglob("*.tar.gz")}
    if not selected_paths.issubset(available_paths):
        missing = sorted(str(path) for path in selected_paths - available_paths)
        raise ValueError(f"selected campaign archives are missing: {missing}")
    excluded_paths = sorted(available_paths - selected_paths)
    selection = {
        "schema_version": 1,
        "selection_rule": "five correctness-qualified fresh-boot sessions per implementation/model/measurement cohort",
        "included_count": len(selected_paths),
        "included": [
            {"path": str(path.relative_to(Path.cwd())), "sha256": sha256_file(path)}
            for path in sorted(selected_paths)
        ],
        "excluded_count": len(excluded_paths),
        "excluded": [
            {
                "path": str(path.relative_to(Path.cwd())),
                "sha256": sha256_file(path),
                "reason": "unintended additional LeNet runs; excluded before final aggregation to preserve balanced five-session cohorts",
            }
            for path in excluded_paths
        ],
    }
    if selection["included_count"] != 40:
        raise ValueError(f"expected 40 selected archives, found {selection['included_count']}")

    summary: dict[str, Any] = {
        "schema_version": 1,
        "qualification": "all selected sessions correctness-qualified",
        "comparison_scope": "system-level INT8 NVDLA versus FP32 four-thread ARM CPU",
        "primary_latency_source": "non-power cohorts",
        "power_scope": "sum of exposed PS and PL rails",
        "selected_sessions": 40,
        "excluded_sessions": len(excluded_paths),
        "models": {},
    }
    for model in MODELS:
        model_result: dict[str, Any] = {"comparison": {}}
        for implementation in IMPLEMENTATIONS:
            latency_cohort = cohorts[(implementation, model, "latency")]
            power_cohort = cohorts[(implementation, model, "power")]
            model_result[implementation] = {
                "latency": {regime: _latency(latency_cohort, implementation, regime) for regime in REGIMES},
                "power": _power(power_cohort, implementation),
                "latency_provenance": latency_cohort["provenance"],
                "power_provenance": power_cohort["provenance"],
            }
        for regime in REGIMES:
            nvdla = model_result["nvdla"]["latency"][regime]
            cpu = model_result["cpu"]["latency"][regime]
            model_result["comparison"][regime] = {
                "cpu_time_divided_by_nvdla_time": cpu["session_median_ms"] / nvdla["session_median_ms"],
                "lower_bound_from_independent_95ci_limits": cpu["ci_lower_ms"] / nvdla["ci_upper_ms"],
                "upper_bound_from_independent_95ci_limits": cpu["ci_upper_ms"] / nvdla["ci_lower_ms"],
            }
        nvdla_power = model_result["nvdla"]["power"]
        cpu_power = model_result["cpu"]["power"]
        model_result["power_comparison"] = {
            "active_power_change_percentage": _change_percentage(
                nvdla_power["active_watts"]["mean"], cpu_power["active_watts"]["mean"]
            ),
            "active_energy_change_percentage": _change_percentage(
                nvdla_power["active_joules_per_inference"]["mean"],
                cpu_power["active_joules_per_inference"]["mean"],
            ),
            "incremental_energy_change_percentage": _change_percentage(
                nvdla_power["incremental_joules_per_inference"]["mean"],
                cpu_power["incremental_joules_per_inference"]["mean"],
            ),
        }
        summary["models"][model] = model_result

    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "campaign-selection.json", selection)
    write_json(out / "campaign-summary.json", summary)
    (out / "latency-comparison.svg").write_text(_latency_svg(summary), encoding="utf-8")
    (out / "relative-latency.svg").write_text(_speedup_svg(summary), encoding="utf-8")
    (out / "monitored-power.svg").write_text(_bar_svg(summary, "power"), encoding="utf-8")
    (out / "monitored-energy.svg").write_text(_bar_svg(summary, "energy"), encoding="utf-8")
    _write_report(summary, out)
    print(f"Final campaign report: {out / 'campaign-report.md'}")
    return 0
