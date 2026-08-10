#!/usr/bin/env python3
"""Create the concise NVDLA evaluation briefing PDF."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph


PAGE_W, PAGE_H = landscape(A4)
MARGIN = 17 * mm
PAGE_COUNT = 8

INK = HexColor("#18212B")
MUTED = HexColor("#62717C")
GRID = HexColor("#DDE4E2")
PAPER = HexColor("#FFFFFF")
PANEL = HexColor("#F4F7F6")
NVDLA = HexColor("#0D8978")
CPU_INT8 = HexColor("#3978A8")
CPU_FP32 = HexColor("#B44B5A")
GOLD = HexColor("#D99A2B")
GREEN = HexColor("#2F8F5B")
BLUE = HexColor("#3978A8")
PALE_GREEN = HexColor("#E8F4EF")
PALE_GOLD = HexColor("#FAF1DE")
PHASE_COLORS = {
    "runtime_initialization": HexColor("#718E9B"),
    "model_loading": CPU_INT8,
    "buffer_preparation": GOLD,
    "runtime_execution": NVDLA,
    "result_handling": HexColor("#78A667"),
    "teardown": CPU_FP32,
    "unprofiled_process_and_launch": HexColor("#AAB5B2"),
}


def fmt_ms(value: float) -> str:
    if value < 10:
        return f"{value:.3f} ms"
    return f"{value:.1f} ms"


def fmt_rate(value: float) -> str:
    if value >= 100:
        return f"{value:.0f} img/s"
    if value >= 10:
        return f"{value:.1f} img/s"
    return f"{value:.2f} img/s"


def paragraph(c: canvas.Canvas, text: str, x: float, y_top: float, width: float,
              size: float = 10, leading: float | None = None,
              color=INK, font: str = "Helvetica", max_height: float = 80 * mm) -> float:
    style = ParagraphStyle(
        "brief",
        fontName=font,
        fontSize=size,
        leading=leading or size * 1.25,
        textColor=color,
        alignment=TA_LEFT,
        spaceAfter=0,
    )
    p = Paragraph(text, style)
    _, height = p.wrap(width, max_height)
    p.drawOn(c, x, y_top - height)
    return height


def page_header(c: canvas.Canvas, section: str, title: str, page: int) -> None:
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(MARGIN, PAGE_H - 12 * mm, section.upper())
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 8.5)
    c.drawRightString(PAGE_W - MARGIN, PAGE_H - 12 * mm, f"NVDLA evaluation brief  |  {page}/{PAGE_COUNT}")
    c.setStrokeColor(GRID)
    c.setLineWidth(0.7)
    c.line(MARGIN, PAGE_H - 15 * mm, PAGE_W - MARGIN, PAGE_H - 15 * mm)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 24)
    c.drawString(MARGIN, PAGE_H - 27 * mm, title)


def footer(c: canvas.Canvas, text: str = "Source: balanced final campaign, five fresh boots per cohort") -> None:
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 7.5)
    c.drawString(MARGIN, 8 * mm, text)
    c.drawRightString(PAGE_W - MARGIN, 8 * mm, "Correctness-qualified samples; no outliers discarded")


def panel(c: canvas.Canvas, x: float, y: float, w: float, h: float, fill=PANEL,
          stroke=GRID) -> None:
    c.setFillColor(fill)
    c.setStrokeColor(stroke)
    c.setLineWidth(0.6)
    c.roundRect(x, y, w, h, 3 * mm, fill=1, stroke=1)


def metric_card(c: canvas.Canvas, x: float, y: float, w: float, h: float,
                value: str, label: str, note: str, accent) -> None:
    panel(c, x, y, w, h, PAPER, GRID)
    c.setFillColor(accent)
    c.rect(x, y, 3 * mm, h, fill=1, stroke=0)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 23)
    c.drawString(x + 8 * mm, y + h - 12 * mm, value)
    paragraph(c, label, x + 8 * mm, y + h - 18 * mm, w - 14 * mm,
              size=9.5, leading=11, font="Helvetica-Bold")
    paragraph(c, note, x + 8 * mm, y + 10 * mm, w - 14 * mm,
              size=7.5, leading=9, color=MUTED)


def legend(c: canvas.Canvas, x: float, y: float) -> None:
    entries = (
        ("NVDLA INT8", NVDLA, 32 * mm),
        ("CPU INT8, 4 threads", CPU_INT8, 39 * mm),
        ("CPU FP32, 4 threads", CPU_FP32, 0),
    )
    for label, color, advance in entries:
        c.setFillColor(color)
        c.circle(x + 3, y + 3, 3, fill=1, stroke=0)
        c.setFillColor(INK)
        c.setFont("Helvetica", 7.8)
        c.drawString(x + 10, y, label)
        x += advance


def add_cpu_int8(data: dict, root: Path) -> None:
    """Attach the independently generated five-session CPU INT8 cohorts."""
    for model in ("lenet", "resnet50"):
        latency_path = root / model / "latency" / "cpu-performance-summary.json"
        power_path = root / model / "power" / "cpu-performance-summary.json"
        latency = json.loads(latency_path.read_text(encoding="utf-8"))
        power = json.loads(power_path.read_text(encoding="utf-8"))
        if latency.get("session_count") != 5 or power.get("session_count") != 5:
            raise ValueError(f"expected five CPU INT8 sessions per cohort for {model}")
        if latency["provenance"].get("precision") != "int8":
            raise ValueError(f"unexpected CPU precision for {model}")

        latency_stats = {}
        for regime, stats in latency["regimes"].items():
            ci = stats["session_median_bootstrap_95ci"]
            latency_stats[regime] = {
                "session_median_ms": ci["estimate_ns"] / 1e6,
                "ci_lower_ms": ci["lower_ns"] / 1e6,
                "ci_upper_ms": ci["upper_ns"] / 1e6,
                "mean_ms": stats["mean_ns"] / 1e6,
            }

        domain = power["power"]["domains"]["MONITORED"]
        active_energy = statistics.mean(
            session["power"]["domains"]["MONITORED"]["active_energy_joules"]
            / session["power"]["executed_inferences"]
            for session in power["sessions"]
        )
        data["models"][model]["cpu_int8"] = {
            "latency": latency_stats,
            "power": {
                "active_watts": {"mean": domain["active_mean_watts"]},
                "incremental_watts": {"mean": domain["incremental_mean_watts"]},
                "active_joules_per_inference": {"mean": active_energy},
                "incremental_joules_per_inference": {
                    "mean": domain["incremental_energy_per_executed_inference_joules"]
                },
            },
            "provenance": latency["provenance"],
        }


def add_supplementary_results(data: dict, root: Path) -> None:
    """Attach input-sensitivity and NVDLA phase evidence."""
    for model in ("lenet", "resnet50"):
        variation_path = root / "input-variation" / model / "input-variation-summary.json"
        performance_path = root / f"nvdla-{model}-latency" / "performance-summary.json"
        variation = json.loads(variation_path.read_text(encoding="utf-8"))
        performance = json.loads(performance_path.read_text(encoding="utf-8"))
        if variation.get("status") != "pass" or variation.get("sessions") != 3:
            raise ValueError(f"expected three passing input-variation sessions for {model}")
        if performance.get("session_count") != 5:
            raise ValueError(f"expected five NVDLA latency sessions for {model}")
        data["models"][model]["input_variation"] = variation
        data["models"][model]["nvdla"]["phases"] = {
            regime: performance["regimes"][regime]["phases"]["aggregates_mean_ns"]
            for regime in ("cold", "warm", "steady")
        }


def draw_summary(c: canvas.Canvas, data: dict) -> None:
    c.setFillColor(PAPER)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    c.setFillColor(NVDLA)
    c.rect(0, PAGE_H - 10 * mm, PAGE_W, 10 * mm, fill=1, stroke=0)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 28)
    c.drawString(MARGIN, PAGE_H - 29 * mm, "NVDLA on PetaLinux 2024.1")
    c.setFont("Helvetica", 15)
    c.setFillColor(MUTED)
    c.drawString(MARGIN, PAGE_H - 39 * mm, "Correctness, latency, throughput, and monitored energy at a glance")

    x_gap = 7 * mm
    y_gap = 7 * mm
    w = (PAGE_W - 2 * MARGIN - 2 * x_gap) / 3
    h = 43 * mm
    y_top = PAGE_H - 54 * mm
    cards = [
        ("100 / 100", "LeNet stability", "One boot; no module reload or PL reset", GREEN),
        ("246 / 246", "ResNet-50 hardware layers", "Exact match to source-built nv_small VP golden", BLUE),
        ("1.34x", "CPU INT8 execution advantage", "378.7 ms CPU INT8 vs 507.7 ms NVDLA on ResNet-50", CPU_INT8),
        ("1.92x", "NVDLA cold-deployment advantage", "1.336 s NVDLA vs 2.566 s CPU INT8 on ResNet-50", NVDLA),
        ("-62.3%", "NVDLA incremental energy", "0.134 J NVDLA vs 0.356 J CPU INT8 on ResNet-50", GOLD),
        ("20 x 2", "Input-sensitivity control", "Balanced image sets; three fresh boots per model", BLUE),
    ]
    for i, item in enumerate(cards):
        row, col = divmod(i, 3)
        metric_card(c, MARGIN + col * (w + x_gap), y_top - (row + 1) * h - row * y_gap,
                    w, h, *item)

    c.setFillColor(PALE_GOLD)
    c.roundRect(MARGIN, 16 * mm, PAGE_W - 2 * MARGIN, 16 * mm, 3 * mm, fill=1, stroke=0)
    paragraph(
        c,
        "<b>Comparison boundary:</b> nv_small NVDLA INT8 is compared with both FP32 and independently quantized QDQ INT8 ONNX Runtime on four Cortex-A53 cores. Equal nominal precision does not imply identical quantization or graph transformation. The primary campaign contains 60 sessions; six supplementary sessions test 20 images per model.",
        MARGIN + 5 * mm,
        28 * mm,
        PAGE_W - 2 * MARGIN - 10 * mm,
        size=8.5,
        leading=10.5,
    )


def draw_correctness(c: canvas.Canvas) -> None:
    page_header(c, "Evaluation coverage", "Correctness before performance", 2)
    footer(c, "Sources: VP configuration audit, differential trace, board stability and ResNet-50 artifacts")

    x = MARGIN
    y0 = 25 * mm
    ladder_w = 52 * mm
    ladder_h = 138 * mm
    panel(c, x, y0, ladder_w, ladder_h, PAPER, GRID)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(x + 7 * mm, y0 + ladder_h - 12 * mm, "Layered acceptance")
    steps = ["Pinned inputs", "ABI and build", "Verified nv_small VP", "Driver probe", "GEM mapping",
             "IRQ + engine completion", "Exact tensor + repeat"]
    top = y0 + ladder_h - 27 * mm
    for i, step in enumerate(steps):
        yy = top - i * 16 * mm
        if i < len(steps) - 1:
            c.setStrokeColor(GRID)
            c.setLineWidth(2)
            c.line(x + 12 * mm, yy - 10 * mm, x + 12 * mm, yy - 16 * mm)
        c.setFillColor(PALE_GREEN)
        c.circle(x + 12 * mm, yy, 5 * mm, fill=1, stroke=0)
        c.setFillColor(GREEN)
        c.setFont("Helvetica-Bold", 10)
        c.drawCentredString(x + 12 * mm, yy - 3, "OK")
        paragraph(c, step, x + 21 * mm, yy + 4 * mm, ladder_w - 27 * mm,
                  size=8.5, leading=10, font="Helvetica-Bold")

    model_x = x + ladder_w + 8 * mm
    model_w = PAGE_W - MARGIN - model_x
    model_h = 65 * mm
    for j, (name, subtitle, accent) in enumerate((
        ("LeNet / MNIST", "Fast repeat oracle", NVDLA),
        ("ResNet-50", "Large multi-engine graph", BLUE),
    )):
        y = y0 + (1 - j) * (model_h + 8 * mm)
        panel(c, model_x, y, model_w, model_h, PAPER, GRID)
        c.setFillColor(accent)
        c.rect(model_x, y, 4 * mm, model_h, fill=1, stroke=0)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 17)
        c.drawString(model_x + 10 * mm, y + model_h - 13 * mm, name)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 9)
        c.drawString(model_x + 10 * mm, y + model_h - 20 * mm, subtitle)
        if j == 0:
            metrics = [
                ("Input", "1 x 1 x 28 x 28"),
                ("Loadable", "0.446 MB"),
                ("Hardware layers", "10"),
                ("Engine mix", "4 Conv / 4 SDP / 2 PDP"),
                ("VP", "Exact output"),
                ("Board", "Exact; 100/100 repeats"),
            ]
            output = "Output: 0 2 0 0 0 0 0 124 0 0"
        else:
            metrics = [
                ("Input", "1 x 3 x 224 x 224"),
                ("Loadable", "25.77 MB"),
                ("Hardware layers", "246"),
                ("Engine mix", "114 Conv / 130 SDP / 2 PDP"),
                ("VP", "246/246; golden established"),
                ("Board", "246/246; exact hash"),
            ]
            output = "Output: 1,000 signed values; SHA-256 842d34f..."
        col_w = (model_w - 20 * mm) / 3
        for i, (label, value) in enumerate(metrics):
            row, col = divmod(i, 3)
            xx = model_x + 10 * mm + col * col_w
            yy = y + (36 * mm if row == 0 else 20 * mm)
            c.setFillColor(MUTED)
            c.setFont("Helvetica", 7.5)
            c.drawString(xx, yy, label.upper())
            c.setFillColor(INK)
            c.setFont("Helvetica-Bold", 8.2)
            c.drawString(xx, yy - 9, value)
        c.setFillColor(PALE_GREEN)
        c.roundRect(model_x + 10 * mm, y + 2 * mm, model_w - 20 * mm, 8 * mm, 2 * mm, fill=1, stroke=0)
        c.setFillColor(GREEN)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(model_x + 14 * mm, y + 4.7 * mm, output)


def draw_input_variation(c: canvas.Canvas, data: dict) -> None:
    page_header(c, "Input sensitivity", "Does execution time depend on the image?", 4)
    footer(c, "Supplementary control: 20 balanced inputs and three fresh boots per model")

    c.setFillColor(PALE_GREEN)
    c.roundRect(MARGIN, 155 * mm, PAGE_W - 2 * MARGIN, 13 * mm, 2.5 * mm, fill=1, stroke=0)
    paragraph(
        c,
        "<b>Execution-validity result:</b> every input produced a repeat-stable output, increased IRQ activity, and completed without a bad kernel pattern.",
        MARGIN + 5 * mm,
        164.5 * mm,
        PAGE_W - 2 * MARGIN - 10 * mm,
        size=8.5,
        leading=10,
        color=GREEN,
    )

    gap = 8 * mm
    pw = (PAGE_W - 2 * MARGIN - gap) / 2
    py, ph = 31 * mm, 117 * mm
    for model_i, model in enumerate(("lenet", "resnet50")):
        x = MARGIN + model_i * (pw + gap)
        item = data["models"][model]["input_variation"]
        title = "LeNet / MNIST" if model == "lenet" else "ResNet-50 / Imagenette"
        repeats = item["runtime_execution"]["count"] // len(item["per_input"])
        med_ns = item["runtime_execution"]["median_ns"]
        per_input = [entry["runtime_execution"]["median_ns"] for entry in item["per_input"]]
        deviations = [(value - med_ns) / med_ns * 100.0 for value in per_input]
        bound = max(abs(min(deviations)), abs(max(deviations))) * 1.18
        accuracy = item["classification"]
        acceptance = "top-1" if model == "lenet" else "top-5"

        panel(c, x, py, pw, ph, PAPER, GRID)
        c.setFillColor(NVDLA)
        c.rect(x, py, 3 * mm, ph, fill=1, stroke=0)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 15)
        c.drawString(x + 9 * mm, py + ph - 12 * mm, title)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 8)
        c.drawString(x + 9 * mm, py + ph - 19 * mm,
                     f"20 inputs x {repeats} measured executions; loaded model and buffers reused")

        plot_x0, plot_x1 = x + 19 * mm, x + pw - 10 * mm
        plot_y = py + 66 * mm
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(x + 9 * mm, py + ph - 32 * mm, "Per-input median execution deviation")
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 7)
        c.drawRightString(x + pw - 10 * mm, py + ph - 32 * mm, "model-specific horizontal scale")
        c.setStrokeColor(GRID)
        c.setLineWidth(1)
        c.line(plot_x0, plot_y, plot_x1, plot_y)

        def xpos(value: float) -> float:
            return plot_x0 + (value + bound) / (2 * bound) * (plot_x1 - plot_x0)

        for value in (-bound, 0.0, bound):
            xx = xpos(value)
            c.setStrokeColor(GRID if value else MUTED)
            c.setLineWidth(0.7 if value else 1.1)
            c.line(xx, plot_y - 10 * mm, xx, plot_y + 10 * mm)
            c.setFillColor(MUTED)
            c.setFont("Helvetica", 6.8)
            label = "0" if value == 0 else f"{value:+.4f}%"
            c.drawCentredString(xx, plot_y - 15 * mm, label)
        for index, value in enumerate(deviations):
            yy = plot_y + ((index % 5) - 2) * 3.4 * mm
            c.setFillColor(NVDLA)
            c.circle(xpos(value), yy, 2.1, fill=1, stroke=0)

        range_pct = item["between_input_median_range_percent"]
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 8.2)
        c.drawString(x + 9 * mm, py + 43 * mm,
                     f"Observed range across the 20 medians: {range_pct:.4f}%")
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 7.2)
        c.drawString(x + 9 * mm, py + 37 * mm, "Input decode and file I/O are outside this execution interval.")

        metric_y = py + 11 * mm
        metric_w = (pw - 23 * mm) / 4
        metrics = (
            (fmt_ms(med_ns / 1e6), "Median execution"),
            (fmt_ms(item["input_update"]["median_ns"] / 1e6), "Prepared input copy"),
            ("20 / 20", "Stable outputs"),
            (f"{accuracy['distinct_input_matches']} / 20", f"Recorded {acceptance}"),
        )
        for i, (value, label) in enumerate(metrics):
            mx = x + 9 * mm + i * (metric_w + 1.7 * mm)
            c.setFillColor(INK if i != 3 else BLUE)
            c.setFont("Helvetica-Bold", 10)
            c.drawString(mx, metric_y + 8 * mm, value)
            paragraph(c, label, mx, metric_y + 4 * mm, metric_w, size=6.7, leading=7.8, color=MUTED)

    c.setFillColor(PALE_GOLD)
    c.roundRect(MARGIN, 14 * mm, PAGE_W - 2 * MARGIN, 11 * mm, 2 * mm, fill=1, stroke=0)
    paragraph(
        c,
        "<b>Interpretation:</b> class accuracy is descriptive model-quality context from a small curated set. Hardware acceptance is based on repeat-stable output, IRQ progress, and clean kernel logs.",
        MARGIN + 5 * mm,
        22 * mm,
        PAGE_W - 2 * MARGIN - 10 * mm,
        size=7.5,
        leading=9,
    )


def draw_latency(c: canvas.Canvas, data: dict) -> None:
    page_header(c, "Latency", "Three deployed stacks across each timing boundary", 3)
    footer(c, "Unpowered cohorts; points are medians across five independent boot-session medians")
    legend(c, PAGE_W - MARGIN - 111 * mm, PAGE_H - 37 * mm)

    x0 = 63 * mm
    x1 = PAGE_W - MARGIN - 8 * mm
    chart_top = PAGE_H - 48 * mm
    chart_bottom = 48 * mm
    min_v, max_v = 0.5, 6000.0

    def xpos(value: float) -> float:
        return x0 + (math.log10(value) - math.log10(min_v)) / (math.log10(max_v) - math.log10(min_v)) * (x1 - x0)

    for tick in (1, 10, 100, 1000, 5000):
        xx = xpos(tick)
        c.setStrokeColor(GRID)
        c.setLineWidth(0.6)
        c.line(xx, chart_bottom, xx, chart_top)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 7.5)
        c.drawCentredString(xx, chart_bottom - 4 * mm, f"{tick:g} ms")

    rows = [
        ("LeNet", "Cold", "lenet", "cold"),
        ("LeNet", "Warm", "lenet", "warm"),
        ("LeNet", "Loaded", "lenet", "steady"),
        ("ResNet-50", "Cold", "resnet50", "cold"),
        ("ResNet-50", "Warm", "resnet50", "warm"),
        ("ResNet-50", "Loaded", "resnet50", "steady"),
    ]
    row_gap = (chart_top - chart_bottom) / len(rows)
    for i, (model_label, regime_label, model, regime) in enumerate(rows):
        y = chart_top - (i + 0.55) * row_gap
        if i == 3:
            c.setStrokeColor(GRID)
            c.setLineWidth(1)
            c.line(MARGIN, y + row_gap * 0.55, x1, y + row_gap * 0.55)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawRightString(x0 - 16 * mm, y + 3, model_label)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 7.5)
        c.drawRightString(x0 - 2 * mm, y + 3, regime_label)
        for stack, color, dy in (
            ("nvdla", NVDLA, 10),
            ("cpu_int8", CPU_INT8, 0),
            ("cpu", CPU_FP32, -10),
        ):
            stat = data["models"][model][stack]["latency"][regime]
            val = stat["session_median_ms"]
            lo, hi = stat["ci_lower_ms"], stat["ci_upper_ms"]
            c.setStrokeColor(color)
            c.setLineWidth(1.6)
            c.line(xpos(lo), y + dy, xpos(hi), y + dy)
            c.setFillColor(color)
            c.circle(xpos(val), y + dy, 3.2, fill=1, stroke=0)
            label = fmt_ms(val)
            xx = xpos(val)
            if stack == "cpu_int8":
                c.setFillColor(color)
                c.setFont("Helvetica-Bold", 7.5)
                c.drawRightString(xx - 5, y + dy - 2.5, label)
            elif xx > x1 - 32 * mm:
                c.setFillColor(color)
                c.setFont("Helvetica-Bold", 7.5)
                c.drawRightString(xx - 5, y + dy - 2.5, label)
            else:
                c.setFillColor(color)
                c.setFont("Helvetica-Bold", 7.5)
                c.drawString(xx + 5, y + dy - 2.5, label)
    ratios = [
        ("LeNet cold", 5.67), ("LeNet warm", 10.33), ("LeNet loaded", 0.41),
        ("ResNet cold", 1.92), ("ResNet warm", 2.58), ("ResNet loaded", 0.75),
    ]
    bx = MARGIN
    by = 16 * mm
    bw = (PAGE_W - 2 * MARGIN - 5 * 4 * mm) / 6
    for i, (label, ratio) in enumerate(ratios):
        xx = bx + i * (bw + 4 * mm)
        fill = PALE_GREEN if ratio > 1 else PALE_GOLD
        panel(c, xx, by, bw, 23 * mm, fill, fill)
        c.setFillColor(GREEN if ratio > 1 else GOLD)
        c.setFont("Helvetica-Bold", 15)
        c.drawCentredString(xx + bw / 2, by + 12 * mm, f"{ratio:.2f}x")
        c.setFillColor(INK)
        c.setFont("Helvetica", 7.2)
        c.drawCentredString(xx + bw / 2, by + 5 * mm, label)
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 7.2)
    c.drawString(MARGIN, 42 * mm, "CPU INT8 time / NVDLA time: above 1.0 favors NVDLA; below 1.0 favors CPU INT8")


def draw_throughput(c: canvas.Canvas, data: dict) -> None:
    page_header(c, "Scale and throughput", "What changes between LeNet and ResNet-50?", 5)
    footer(c, "Throughput is reciprocal mean latency, not concurrent pipelined throughput")

    left = MARGIN
    mid = PAGE_W / 2 + 2 * mm
    top = PAGE_H - 45 * mm
    panel(c, left, 24 * mm, PAGE_W / 2 - MARGIN - 6 * mm, top - 24 * mm, PAPER, GRID)
    panel(c, mid, 24 * mm, PAGE_W / 2 - MARGIN - 6 * mm, top - 24 * mm, PAPER, GRID)

    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(left + 7 * mm, top - 10 * mm, "Loaded-context throughput")
    c.drawString(mid + 7 * mm, top - 10 * mm, "Workload scale and operation mix")

    for j, model in enumerate(("lenet", "resnet50")):
        label = "LeNet" if model == "lenet" else "ResNet-50"
        y = top - 35 * mm - j * 53 * mm
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(left + 8 * mm, y + 15 * mm, label)
        rates = {}
        for stack in ("nvdla", "cpu_int8", "cpu"):
            mean_ms = data["models"][model][stack]["latency"]["steady"]["mean_ms"]
            rates[stack] = 1000.0 / mean_ms
        max_rate = max(rates.values()) * 1.08
        for i, (stack, stack_label, color) in enumerate((
            ("nvdla", "NVDLA", NVDLA),
            ("cpu_int8", "CPU INT8", CPU_INT8),
            ("cpu", "CPU FP32", CPU_FP32),
        )):
            yy = y + 5 * mm - i * 7.5 * mm
            width = 62 * mm * rates[stack] / max_rate
            c.setFillColor(MUTED)
            c.setFont("Helvetica", 6.8)
            c.drawRightString(left + 34 * mm, yy + 1.3 * mm, stack_label)
            c.setFillColor(color)
            c.roundRect(left + 36 * mm, yy, width, 5.5 * mm, 1.5 * mm, fill=1, stroke=0)
            c.setFillColor(INK)
            c.setFont("Helvetica-Bold", 8)
            c.drawString(left + 38 * mm + width, yy + 1, fmt_rate(rates[stack]))

    complexity = {
        "lenet": {"size": "0.446 MB", "hwls": 10, "ops": [("Conv", 4, NVDLA), ("SDP", 4, GOLD), ("PDP", 2, BLUE)]},
        "resnet50": {"size": "25.77 MB", "hwls": 246, "ops": [("Conv", 114, NVDLA), ("SDP", 130, GOLD), ("PDP", 2, BLUE)]},
    }
    for j, model in enumerate(("lenet", "resnet50")):
        info = complexity[model]
        label = "LeNet" if model == "lenet" else "ResNet-50"
        y = top - 28 * mm - j * 56 * mm
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(mid + 8 * mm, y, label)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 8)
        cpu_sizes = "CPU ONNX: 1.73 MB FP32 / 0.45 MB INT8" if model == "lenet" else "CPU ONNX: 102.48 MB FP32 / 26.09 MB INT8"
        c.drawString(mid + 8 * mm, y - 7 * mm, f"NVDLA: {info['size']} loadable  |  {info['hwls']} hardware layers")
        c.setFont("Helvetica", 7.3)
        c.drawString(mid + 8 * mm, y - 12 * mm, cpu_sizes)
        bar_x, bar_y, bar_w = mid + 8 * mm, y - 23 * mm, 101 * mm
        total = sum(v for _, v, _ in info["ops"])
        cursor = bar_x
        for name, value, color in info["ops"]:
            width = bar_w * value / total
            c.setFillColor(color)
            c.rect(cursor, bar_y, width, 8 * mm, fill=1, stroke=0)
            if width > 12 * mm:
                c.setFillColor(PAPER)
                c.setFont("Helvetica-Bold", 7.5)
                c.drawCentredString(cursor + width / 2, bar_y + 2.7 * mm, f"{name} {value}")
            cursor += width
        if model == "resnet50":
            c.setFillColor(BLUE)
            c.circle(bar_x + bar_w + 4 * mm, bar_y + 4 * mm, 2.3, fill=1, stroke=0)
            c.setFillColor(INK)
            c.setFont("Helvetica", 7)
            c.drawString(bar_x + bar_w + 8 * mm, bar_y + 2.2 * mm, "PDP 2")

    c.setFillColor(PALE_GOLD)
    c.roundRect(mid + 8 * mm, 32 * mm, 107 * mm, 19 * mm, 2 * mm, fill=1, stroke=0)
    paragraph(c, "ResNet-50 has <b>24.6x</b> more hardware layers and a <b>57.8x</b> larger loadable. Its larger execution graph amortizes fixed submission and interrupt costs.",
              mid + 13 * mm, 47 * mm, 97 * mm, size=8.5, leading=10.5)


def draw_phase_breakdown(c: canvas.Canvas, data: dict) -> None:
    page_header(c, "Latency composition", "Where does NVDLA deployment time go?", 6)
    footer(c, "Phase means from five fresh-boot latency sessions; no power sampling")

    phase_specs = (
        ("runtime_initialization", "Initialization"),
        ("model_loading", "Model loading"),
        ("buffer_preparation", "Input + buffers"),
        ("runtime_execution", "Runtime execution"),
        ("result_handling", "Result handling"),
        ("teardown", "Teardown"),
        ("unprofiled_process_and_launch", "Launch / unprofiled"),
    )
    legend_x = MARGIN
    legend_y = PAGE_H - 40 * mm
    for key, label in phase_specs:
        c.setFillColor(PHASE_COLORS[key])
        c.rect(legend_x, legend_y, 4 * mm, 4 * mm, fill=1, stroke=0)
        c.setFillColor(INK)
        c.setFont("Helvetica", 7.2)
        c.drawString(legend_x + 6 * mm, legend_y + 0.5 * mm, label)
        legend_x += stringWidth(label, "Helvetica", 7.2) + 13 * mm

    panel_h = 56 * mm
    panel_w = PAGE_W - 2 * MARGIN
    for model_i, model in enumerate(("lenet", "resnet50")):
        y = 94 * mm if model_i == 0 else 31 * mm
        panel(c, MARGIN, y, panel_w, panel_h, PAPER, GRID)
        phases = data["models"][model]["nvdla"]["phases"]
        title = "LeNet" if model == "lenet" else "ResNet-50"
        cold_total = sum(phases["cold"].values())
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(MARGIN + 8 * mm, y + panel_h - 10 * mm, title)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 7.3)
        c.drawString(MARGIN + 30 * mm, y + panel_h - 10 * mm,
                     "bar length is relative to this model's cold launch-to-exit time")

        bar_x = MARGIN + 41 * mm
        bar_w = panel_w - 72 * mm
        bar_h = 7 * mm
        for row_i, (regime, regime_label) in enumerate((
            ("cold", "Cold launch"),
            ("warm", "Warm launch"),
            ("steady", "Loaded context"),
        )):
            yy = y + 30 * mm - row_i * 11 * mm
            values = phases[regime]
            total = sum(values.values())
            c.setFillColor(INK)
            c.setFont("Helvetica-Bold", 7.8)
            c.drawRightString(bar_x - 4 * mm, yy + 2.2 * mm, regime_label)
            c.setFillColor(PANEL)
            c.roundRect(bar_x, yy, bar_w, bar_h, 1.2 * mm, fill=1, stroke=0)
            cursor = bar_x
            for key, label in phase_specs:
                value = values.get(key, 0.0)
                width = bar_w * value / cold_total
                if width <= 0:
                    continue
                c.setFillColor(PHASE_COLORS[key])
                c.rect(cursor, yy, width, bar_h, fill=1, stroke=0)
                if width >= 24 * mm:
                    share = value / total * 100.0
                    c.setFillColor(PAPER if key != "unprofiled_process_and_launch" else INK)
                    c.setFont("Helvetica-Bold", 6.6)
                    short = {
                        "runtime_initialization": "Init",
                        "model_loading": "Model",
                        "buffer_preparation": "Buffers",
                        "runtime_execution": "Execution",
                        "result_handling": "Result",
                        "teardown": "Teardown",
                        "unprofiled_process_and_launch": "Launch",
                    }[key]
                    c.drawCentredString(cursor + width / 2, yy + 2.2 * mm, f"{short} {share:.1f}%")
                cursor += width
            c.setFillColor(INK)
            c.setFont("Helvetica-Bold", 7.8)
            c.drawString(bar_x + bar_w + 4 * mm, yy + 2.2 * mm, fmt_ms(total / 1e6))

        warm_total = sum(phases["warm"].values())
        steady_total = sum(phases["steady"].values())
        warm_execution_share = phases["warm"]["runtime_execution"] / warm_total * 100.0
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 7)
        c.drawRightString(MARGIN + panel_w - 8 * mm, y + panel_h - 10 * mm,
                          f"Warm runtime execution share {warm_execution_share:.1f}%  |  loaded context {fmt_ms(steady_total / 1e6)}")

    c.setFillColor(PALE_GOLD)
    c.roundRect(MARGIN, 14 * mm, PAGE_W - 2 * MARGIN, 10 * mm, 2 * mm, fill=1, stroke=0)
    paragraph(
        c,
        "<b>Timing boundary:</b> cold and warm bars span process launch to exit. Loaded context reuses the model and buffers; its blocking runtime execution includes userspace dispatch, ioctl/KMD scheduling, accelerator work, IRQ completion, and emulator tasks.",
        MARGIN + 5 * mm,
        21.5 * mm,
        PAGE_W - 2 * MARGIN - 10 * mm,
        size=7.2,
        leading=8.5,
    )


def draw_power(c: canvas.Canvas, data: dict) -> None:
    page_header(c, "Power and energy", "Monitored PS + PL rails during inference", 7)
    footer(c, "Power cohorts are separate from primary latency cohorts; 50 ms sampling with endpoint capture")
    legend(c, PAGE_W - MARGIN - 111 * mm, PAGE_H - 37 * mm)

    chart_y = 99 * mm
    chart_h = 58 * mm
    group_w = 76 * mm
    centers = [MARGIN + 45 * mm, MARGIN + 135 * mm, MARGIN + 225 * mm]
    titles = [
        ("Active power", "watts"),
        ("Active energy", "per inference"),
        ("Incremental energy", "above idle, per inference"),
    ]
    for cx, (title, subtitle) in zip(centers, titles):
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 10.5)
        c.drawCentredString(cx, chart_y + chart_h + 8 * mm, title)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 7.5)
        c.drawCentredString(cx, chart_y + chart_h + 3.5 * mm, subtitle)

    def metric(model: str, stack: str, key: str) -> float:
        return data["models"][model][stack]["power"][key]["mean"]

    # Active power, shared W scale.
    max_power = 4.0
    for m_i, model in enumerate(("lenet", "resnet50")):
        gx = centers[0] - group_w / 2 + m_i * 39 * mm
        for s_i, (stack, color) in enumerate((
            ("nvdla", NVDLA), ("cpu_int8", CPU_INT8), ("cpu", CPU_FP32)
        )):
            val = metric(model, stack, "active_watts")
            bh = chart_h * val / max_power
            bx = gx + s_i * 7.5 * mm
            c.setFillColor(color)
            c.roundRect(bx, chart_y, 7 * mm, bh, 1.2 * mm, fill=1, stroke=0)
            c.setFillColor(INK)
            c.setFont("Helvetica-Bold", 7)
            c.drawCentredString(bx + 3.5 * mm, chart_y + bh + 2 * mm, f"{val:.2f} W")
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 7.5)
        c.drawCentredString(gx + 11 * mm, chart_y - 5 * mm, "LeNet" if model == "lenet" else "ResNet-50")

    # Active and incremental energy use model-specific scales and labels.
    for panel_i, key in enumerate(("active_joules_per_inference", "incremental_joules_per_inference"), start=1):
        for m_i, model in enumerate(("lenet", "resnet50")):
            vals = [metric(model, stack, key) for stack in ("nvdla", "cpu_int8", "cpu")]
            scale = max(vals) * 1.15
            gx = centers[panel_i] - group_w / 2 + m_i * 39 * mm
            for s_i, (stack, color) in enumerate((
                ("nvdla", NVDLA), ("cpu_int8", CPU_INT8), ("cpu", CPU_FP32)
            )):
                val = vals[s_i]
                bh = chart_h * val / scale
                bx = gx + s_i * 7.5 * mm
                c.setFillColor(color)
                c.roundRect(bx, chart_y, 7 * mm, bh, 1.2 * mm, fill=1, stroke=0)
                display = f"{val * 1000:.3f} mJ" if model == "lenet" else f"{val:.3f} J"
                c.setFillColor(INK)
                c.setFont("Helvetica-Bold", 6.8)
                c.saveState()
                c.translate(bx + 3.5 * mm, chart_y + bh + 2 * mm)
                c.rotate(60)
                c.drawString(0, 0, display)
                c.restoreState()
            c.setFillColor(MUTED)
            c.setFont("Helvetica", 7.5)
            c.drawCentredString(gx + 11 * mm, chart_y - 5 * mm, "LeNet" if model == "lenet" else "ResNet-50")

    # Horizontal grid line and interpretation cards.
    c.setStrokeColor(GRID)
    c.line(MARGIN, chart_y, PAGE_W - MARGIN, chart_y)
    cards = [
        ("LeNet", "+28.7%", "NVDLA active energy", "-7.1% incremental", PALE_GOLD),
        ("ResNet-50", "+21.8%", "NVDLA active energy", "-62.3% incremental", PALE_GREEN),
    ]
    card_w = (PAGE_W - 2 * MARGIN - 8 * mm) / 2
    for i, (model, value, label, note, fill) in enumerate(cards):
        x = MARGIN + i * (card_w + 8 * mm)
        y = 28 * mm
        panel(c, x, y, card_w, 52 * mm, fill, fill)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(x + 8 * mm, y + 38 * mm, model)
        c.setFillColor(GOLD)
        c.setFont("Helvetica-Bold", 23)
        c.drawString(x + 8 * mm, y + 23 * mm, value)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(x + 42 * mm, y + 27 * mm, label)
        c.setFillColor(GREEN)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(x + 42 * mm, y + 17 * mm, note)
        paragraph(c, "Change is NVDLA relative to CPU INT8. Active includes the monitored platform; incremental subtracts driver-loaded idle.",
                  x + 8 * mm, y + 12 * mm, card_w - 16 * mm, size=7.5, leading=9, color=MUTED)


def draw_method(c: canvas.Canvas) -> None:
    page_header(c, "Measurement design", "What was collected, and how to read it", 8)
    footer(c, "Full provenance and raw samples remain in artifacts/final-reports and the selected session archives")

    # Protocol flow.
    flow_y = PAGE_H - 61 * mm
    steps = [
        ("1", "Fresh boot", "Unique Linux boot ID"),
        ("2", "Settle + verify", "Clock, frequency, hashes"),
        ("3", "Correctness gate", "Golden output + kernel health"),
        ("4", "Measure", "Unpowered latency or powered batch"),
        ("5", "Archive", "Raw profiles, rails, logs"),
        ("6", "Aggregate", "Five session medians + 95% CI"),
    ]
    gap = 4 * mm
    sw = (PAGE_W - 2 * MARGIN - 5 * gap) / 6
    for i, (num, title, note) in enumerate(steps):
        x = MARGIN + i * (sw + gap)
        panel(c, x, flow_y - 27 * mm, sw, 27 * mm, PAPER, GRID)
        c.setFillColor(NVDLA if i < 4 else BLUE)
        c.circle(x + 8 * mm, flow_y - 8 * mm, 4 * mm, fill=1, stroke=0)
        c.setFillColor(PAPER)
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(x + 8 * mm, flow_y - 10.5 * mm, num)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 8.2)
        c.drawString(x + 15 * mm, flow_y - 9 * mm, title)
        paragraph(c, note, x + 5 * mm, flow_y - 16 * mm, sw - 10 * mm,
                  size=6.8, leading=8, color=MUTED)
        if i < len(steps) - 1:
            c.setStrokeColor(GRID)
            c.setLineWidth(1.5)
            c.line(x + sw, flow_y - 13.5 * mm, x + sw + gap, flow_y - 13.5 * mm)

    # Metric inventory.
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(MARGIN, flow_y - 42 * mm, "Metric inventory")
    inventory = [
        ("Correctness", "Output hashes, exact tensors, engine sequence, IRQ deltas, repeat failures", GREEN),
        ("Workload", "Input shape, loadable/model size, HWL count, per-engine operation count", BLUE),
        ("Latency", "Cold deployment, warm deployment, loaded-context execution, runtime phases", NVDLA),
        ("Throughput", "Images/s for every timing boundary; CPU/NVDLA latency ratios", NVDLA),
        ("Power", "18 PS + PL rails, idle/active/incremental watts, integrated energy/inference", GOLD),
        ("Environment", "Kernel, binary hashes, clock, CPU affinity/frequency/governor, boot ID", CPU_FP32),
        ("Statistics", "Raw samples, mean/median, spread, percentiles, retained outliers, bootstrap CI", BLUE),
        ("Evidence", "Serial, dmesg, runtime profiles, sensor samples, manifests, selection hashes", GREEN),
    ]
    inv_top = flow_y - 52 * mm
    col_gap = 7 * mm
    iw = (PAGE_W - 2 * MARGIN - col_gap) / 2
    ih = 15 * mm
    for i, (title, note, accent) in enumerate(inventory):
        row, col = divmod(i, 2)
        x = MARGIN + col * (iw + col_gap)
        y = inv_top - (row + 1) * ih - row * 2 * mm
        panel(c, x, y, iw, ih, PAPER, GRID)
        c.setFillColor(accent)
        c.rect(x, y, 2.5 * mm, ih, fill=1, stroke=0)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 8.2)
        c.drawString(x + 7 * mm, y + 9 * mm, title)
        paragraph(c, note, x + 31 * mm, y + 11.5 * mm, iw - 36 * mm,
                  size=6.8, leading=8, color=MUTED)

    c.setFillColor(PALE_GOLD)
    c.roundRect(MARGIN, 15 * mm, PAGE_W - 2 * MARGIN, 14 * mm, 2 * mm, fill=1, stroke=0)
    paragraph(c, "<b>Interpret with:</b> one ZCU102 and nv_small implementation; NVDLA INT8 versus independently quantized CPU INT8 plus an FP32 reference; monitored rails rather than wall input; five boots per primary cohort and three per input-sensitivity model.",
              MARGIN + 5 * mm, 26 * mm, PAGE_W - 2 * MARGIN - 10 * mm,
              size=8, leading=9.5)


def build_pdf(source: Path, cpu_int8_root: Path, report_root: Path, output: Path) -> None:
    data = json.loads(source.read_text(encoding="utf-8"))
    if data.get("selected_sessions") != 40:
        raise ValueError("expected the balanced 40-session NVDLA/CPU FP32 campaign")
    add_cpu_int8(data, cpu_int8_root)
    add_supplementary_results(data, report_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(output), pagesize=(PAGE_W, PAGE_H), pageCompression=1)
    c.setTitle("NVDLA Evaluation Metrics and Results")
    c.setAuthor("NVDLA PetaLinux project")
    c.setSubject("Correctness, latency, throughput, power, and FP32/INT8 ARM CPU comparison")
    draw_summary(c, data)
    c.showPage()
    draw_correctness(c)
    c.showPage()
    draw_latency(c, data)
    c.showPage()
    draw_input_variation(c, data)
    c.showPage()
    draw_throughput(c, data)
    c.showPage()
    draw_phase_breakdown(c, data)
    c.showPage()
    draw_power(c, data)
    c.showPage()
    draw_method(c)
    c.showPage()
    c.save()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("artifacts/final-reports/comparison/campaign-summary.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/pdf/nvdla-evaluation-brief.pdf"),
    )
    parser.add_argument(
        "--cpu-int8-root",
        type=Path,
        default=Path("artifacts/final-reports/cpu-int8"),
    )
    parser.add_argument(
        "--report-root",
        type=Path,
        default=Path("artifacts/final-reports"),
    )
    args = parser.parse_args()
    build_pdf(args.source, args.cpu_int8_root, args.report_root, args.output)
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
