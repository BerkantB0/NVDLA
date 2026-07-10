#!/usr/bin/env python3
"""Render the modern Linux milestone Markdown report as a typeset PDF."""

from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    ListFlowable,
    ListItem,
    LongTable,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents


PAGE_WIDTH, PAGE_HEIGHT = A4
LEFT_MARGIN = 25 * mm
RIGHT_MARGIN = 25 * mm
TOP_MARGIN = 23 * mm
BOTTOM_MARGIN = 21 * mm
CONTENT_WIDTH = PAGE_WIDTH - LEFT_MARGIN - RIGHT_MARGIN

rl_config.invariant = 1


def register_fonts() -> None:
    """Use Liberation fonts when present and built-in PDF fonts otherwise."""
    candidates = [
        Path("C:/Windows/Fonts"),
        Path("/usr/share/fonts/truetype/liberation2"),
    ]
    font_files = {
        "ReportSerif": "LiberationSerif-Regular.ttf",
        "ReportSerif-Bold": "LiberationSerif-Bold.ttf",
        "ReportSerif-Italic": "LiberationSerif-Italic.ttf",
        "ReportMono": "LiberationMono-Regular.ttf",
    }
    found: dict[str, Path] = {}
    for directory in candidates:
        for font_name, filename in font_files.items():
            path = directory / filename
            if path.exists() and font_name not in found:
                found[font_name] = path

    if len(found) == len(font_files):
        for font_name, path in found.items():
            pdfmetrics.registerFont(TTFont(font_name, str(path)))
        pdfmetrics.registerFontFamily(
            "ReportSerif",
            normal="ReportSerif",
            bold="ReportSerif-Bold",
            italic="ReportSerif-Italic",
            boldItalic="ReportSerif-Bold",
        )
        return

    font_files.clear()


def font_name(role: str) -> str:
    registered = "ReportSerif" in pdfmetrics.getRegisteredFontNames()
    if role == "regular":
        return "ReportSerif" if registered else "Times-Roman"
    if role == "bold":
        return "ReportSerif-Bold" if registered else "Times-Bold"
    if role == "italic":
        return "ReportSerif-Italic" if registered else "Times-Italic"
    return "ReportMono" if registered else "Courier"


def build_styles() -> dict[str, ParagraphStyle]:
    sample = getSampleStyleSheet()
    body = ParagraphStyle(
        "ReportBody",
        parent=sample["BodyText"],
        fontName=font_name("regular"),
        fontSize=10.3,
        leading=14.1,
        alignment=TA_JUSTIFY,
        textColor=colors.HexColor("#171717"),
        spaceAfter=7,
        splitLongWords=True,
        allowWidows=0,
        allowOrphans=0,
    )
    return {
        "body": body,
        "body_left": ParagraphStyle(
            "ReportBodyLeft",
            parent=body,
            alignment=TA_LEFT,
        ),
        "heading1": ParagraphStyle(
            "ReportHeading1",
            parent=body,
            fontName=font_name("bold"),
            fontSize=20,
            leading=24,
            alignment=TA_LEFT,
            spaceBefore=0,
            spaceAfter=16,
            keepWithNext=True,
        ),
        "heading2": ParagraphStyle(
            "ReportHeading2",
            parent=body,
            fontName=font_name("bold"),
            fontSize=13.5,
            leading=17,
            alignment=TA_LEFT,
            spaceBefore=12,
            spaceAfter=7,
            keepWithNext=True,
        ),
        "caption": ParagraphStyle(
            "ReportCaption",
            parent=body,
            fontName=font_name("italic"),
            fontSize=9,
            leading=11.5,
            alignment=TA_CENTER,
            spaceBefore=3,
            spaceAfter=10,
        ),
        "list": ParagraphStyle(
            "ReportList",
            parent=body,
            alignment=TA_LEFT,
            leftIndent=0,
            firstLineIndent=0,
            spaceAfter=2,
        ),
        "code": ParagraphStyle(
            "ReportCode",
            parent=body,
            fontName=font_name("mono"),
            fontSize=7.4,
            leading=9.4,
            alignment=TA_LEFT,
            leftIndent=7,
            rightIndent=7,
            borderColor=colors.HexColor("#b8b8b8"),
            borderWidth=0.5,
            borderPadding=7,
            backColor=colors.HexColor("#f5f5f2"),
            spaceBefore=4,
            spaceAfter=10,
        ),
        "table": ParagraphStyle(
            "ReportTableCell",
            parent=body,
            fontSize=7.6,
            leading=9.4,
            alignment=TA_LEFT,
            spaceAfter=0,
        ),
        "table_header": ParagraphStyle(
            "ReportTableHeader",
            parent=body,
            fontName=font_name("bold"),
            fontSize=7.8,
            leading=9.5,
            alignment=TA_LEFT,
            textColor=colors.white,
            spaceAfter=0,
        ),
        "toc_title": ParagraphStyle(
            "TOCTitle",
            parent=body,
            fontName=font_name("bold"),
            fontSize=21,
            leading=25,
            alignment=TA_LEFT,
            spaceAfter=20,
        ),
        "title_kicker": ParagraphStyle(
            "TitleKicker",
            parent=body,
            fontName=font_name("bold"),
            fontSize=12,
            leading=16,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#333333"),
            spaceAfter=5,
        ),
        "title": ParagraphStyle(
            "Title",
            parent=body,
            fontName=font_name("bold"),
            fontSize=25,
            leading=31,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#111111"),
            spaceAfter=16,
        ),
        "title_meta": ParagraphStyle(
            "TitleMeta",
            parent=body,
            fontSize=11,
            leading=16,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#333333"),
        ),
    }


def inline_markup(text: str, code_size: float | None = 8.3) -> str:
    """Translate the small Markdown inline subset used by the report."""
    escaped = html.escape(text, quote=False)
    code_spans: list[str] = []

    def hold_code(match: re.Match[str]) -> str:
        code_spans.append(match.group(1))
        return f"@@CODE{len(code_spans) - 1}@@"

    escaped = re.sub(r"`([^`]+)`", hold_code, escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", escaped)
    for index, value in enumerate(code_spans):
        size_attribute = f' size="{code_size}"' if code_size is not None else ""
        escaped = escaped.replace(
            f"@@CODE{index}@@",
            f'<font name="{font_name("mono")}"{size_attribute}>{value}</font>',
        )
    return escaped


def is_table_separator(line: str) -> bool:
    cells = split_table_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def split_table_row(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]

    cells: list[str] = []
    current: list[str] = []
    in_code = False
    for character in text:
        if character == "`":
            in_code = not in_code
        if character == "|" and not in_code:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    cells.append("".join(current).strip())
    return cells


def table_widths(headers: list[str], column_count: int) -> list[float]:
    key = tuple(header.lower() for header in headers)
    ratios: dict[tuple[str, ...], list[float]] = {
        ("criterion", "required evidence"): [0.27, 0.73],
        ("component", "tested version or revision"): [0.25, 0.75],
        ("patch", "change", "reason"): [0.12, 0.34, 0.54],
        ("stage", "test", "faults isolated"): [0.10, 0.29, 0.61],
        ("layer", "recorded value", "result"): [0.27, 0.53, 0.20],
        ("metric", "result"): [0.37, 0.63],
        ("criterion", "evaluation"): [0.25, 0.75],
        ("evidence", "run or tracked source", "purpose"): [0.11, 0.39, 0.50],
    }
    selected = ratios.get(key)
    if selected is None:
        selected = [1 / column_count] * column_count
    return [CONTENT_WIDTH * ratio for ratio in selected]


def make_table(rows: list[list[str]], styles: dict[str, ParagraphStyle]) -> LongTable:
    column_count = len(rows[0])
    normalised = [row + [""] * (column_count - len(row)) for row in rows]
    data = []
    for row_index, row in enumerate(normalised):
        style = styles["table_header"] if row_index == 0 else styles["table"]
        data.append(
            [Paragraph(inline_markup(cell, code_size=6.8), style) for cell in row[:column_count]]
        )

    table = LongTable(
        data,
        colWidths=table_widths(normalised[0], column_count),
        repeatRows=1,
        hAlign="LEFT",
        splitByRow=True,
        spaceBefore=5,
        spaceAfter=12,
    )
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#343a40")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#9c9c9c")),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for row_index in range(1, len(data)):
        if row_index % 2 == 0:
            commands.append(
                ("BACKGROUND", (0, row_index), (-1, row_index), colors.HexColor("#f2f2ef"))
            )
    table.setStyle(TableStyle(commands))
    return table


def consume_list(lines: list[str], start: int) -> tuple[list[tuple[str, str]], int]:
    first = re.match(r"^\s*(?:(\d+)\.|(-))\s+(.*)$", lines[start])
    if not first:
        return [], start
    expected_kind = "number" if first.group(1) else "bullet"
    items: list[tuple[str, str]] = []
    index = start
    while index < len(lines):
        match = re.match(r"^\s*(?:(\d+)\.|(-))\s+(.*)$", lines[index])
        if not match:
            break
        kind = "number" if match.group(1) else "bullet"
        if kind != expected_kind:
            break
        value = match.group(3).strip()
        index += 1
        continuation: list[str] = []
        while index < len(lines):
            line = lines[index]
            if not line.strip():
                break
            if re.match(r"^\s*(?:(\d+)\.|(-))\s+", line):
                break
            if line.startswith("  "):
                continuation.append(line.strip())
                index += 1
                continue
            break
        if continuation:
            value = " ".join([value, *continuation])
        items.append((expected_kind, value))
        if index < len(lines) and not lines[index].strip():
            lookahead = index + 1
            while lookahead < len(lines) and not lines[lookahead].strip():
                lookahead += 1
            if lookahead < len(lines):
                next_item = re.match(r"^\s*(?:(\d+)\.|(-))\s+", lines[lookahead])
                if next_item:
                    next_kind = "number" if next_item.group(1) else "bullet"
                    if next_kind == expected_kind:
                        index = lookahead
                        continue
            break
    return items, index


def make_list(items: list[tuple[str, str]], styles: dict[str, ParagraphStyle]) -> ListFlowable:
    kind = items[0][0]
    flow_items = [
        ListItem(Paragraph(inline_markup(value), styles["list"]), leftIndent=12)
        for _, value in items
    ]
    return ListFlowable(
        flow_items,
        bulletType="1" if kind == "number" else "bullet",
        start="1" if kind == "number" else "-",
        leftIndent=22,
        bulletFontName=font_name("regular"),
        bulletFontSize=9.5,
        bulletOffsetY=1,
        spaceBefore=2,
        spaceAfter=8,
    )


def heading_flow(text: str, level: int, styles: dict[str, ParagraphStyle]) -> Paragraph:
    style = styles["heading1"] if level == 1 else styles["heading2"]
    paragraph = Paragraph(inline_markup(text, code_size=None), style)
    paragraph.toc_level = level - 1
    return paragraph


def parse_blocks(
    lines: list[str],
    styles: dict[str, ParagraphStyle],
    *,
    page_break_headings: bool,
) -> list:
    flows: list = []
    index = 0
    first_heading = True
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue

        heading = re.match(r"^(#{2,3})\s+(.+)$", line)
        if heading:
            level = 1 if len(heading.group(1)) == 2 else 2
            if level == 1 and page_break_headings and not first_heading:
                flows.append(PageBreak())
            flows.append(heading_flow(heading.group(2).strip(), level, styles))
            first_heading = False
            index += 1
            continue

        if line.startswith("```"):
            index += 1
            code_lines: list[str] = []
            while index < len(lines) and not lines[index].startswith("```"):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            flows.append(Preformatted("\n".join(code_lines), styles["code"], maxLineLength=96))
            continue

        if line.lstrip().startswith("|") and index + 1 < len(lines) and is_table_separator(lines[index + 1]):
            rows = [split_table_row(line)]
            index += 2
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                rows.append(split_table_row(lines[index]))
                index += 1
            flows.append(make_table(rows, styles))
            continue

        list_match = re.match(r"^\s*(?:(\d+)\.|(-))\s+", line)
        if list_match:
            items, index = consume_list(lines, index)
            flows.append(make_list(items, styles))
            continue

        if re.fullmatch(r"\*Figure .+\*", line.strip()):
            flows.append(Paragraph(inline_markup(line.strip()[1:-1]), styles["caption"]))
            index += 1
            continue

        paragraph_lines = [line.strip()]
        index += 1
        while index < len(lines):
            next_line = lines[index]
            if not next_line.strip():
                break
            if re.match(r"^(#{2,3})\s+", next_line):
                break
            if next_line.startswith("```"):
                break
            if next_line.lstrip().startswith("|") and index + 1 < len(lines) and is_table_separator(lines[index + 1]):
                break
            if re.match(r"^\s*(?:(\d+)\.|(-))\s+", next_line):
                break
            paragraph_lines.append(next_line.strip())
            index += 1
        paragraph_text = " ".join(paragraph_lines)
        has_long_code = bool(re.search(r"`[^`]{18,}`", paragraph_text))
        is_reference = bool(re.match(r"^\[\d+\]", paragraph_text))
        paragraph_style = styles["body_left"] if has_long_code or is_reference else styles["body"]
        flows.append(Paragraph(inline_markup(paragraph_text), paragraph_style))

    return flows


class MilestoneDocTemplate(BaseDocTemplate):
    def __init__(self, output: Path, title: str, author: str):
        super().__init__(
            str(output),
            pagesize=A4,
            leftMargin=LEFT_MARGIN,
            rightMargin=RIGHT_MARGIN,
            topMargin=TOP_MARGIN,
            bottomMargin=BOTTOM_MARGIN,
            title=title,
            author=author,
            subject="Modern Linux NVDLA KMD and UMD milestone report",
        )
        frame = Frame(
            LEFT_MARGIN,
            BOTTOM_MARGIN,
            CONTENT_WIDTH,
            PAGE_HEIGHT - TOP_MARGIN - BOTTOM_MARGIN,
            id="report-frame",
        )
        self.addPageTemplates(PageTemplate(id="report", frames=[frame], onPage=self.draw_page))
        self._bookmark_index = 0
        self.report_title = title

    def draw_page(self, canvas, doc) -> None:
        page_number = canvas.getPageNumber()
        canvas.saveState()
        canvas.setTitle(self.title)
        canvas.setAuthor(self.author)
        if page_number > 1:
            canvas.setStrokeColor(colors.HexColor("#b0b0b0"))
            canvas.setLineWidth(0.4)
            canvas.line(LEFT_MARGIN, PAGE_HEIGHT - 15 * mm, PAGE_WIDTH - RIGHT_MARGIN, PAGE_HEIGHT - 15 * mm)
            canvas.setFont(font_name("regular"), 8)
            canvas.setFillColor(colors.HexColor("#555555"))
            canvas.drawString(LEFT_MARGIN, PAGE_HEIGHT - 12 * mm, "Milestone 1 - NVDLA Software Stack")
            canvas.drawCentredString(PAGE_WIDTH / 2, 10 * mm, str(page_number - 1))
        canvas.restoreState()

    def afterFlowable(self, flowable) -> None:
        if not isinstance(flowable, Paragraph) or not hasattr(flowable, "toc_level"):
            return
        level = flowable.toc_level
        text = flowable.getPlainText()
        key = getattr(flowable, "bookmark_name", None)
        if key is None:
            key = f"heading-{self._bookmark_index}"
            flowable.bookmark_name = key
            self._bookmark_index += 1
        self.canv.bookmarkPage(key)
        self.canv.addOutlineEntry(text, key, level=level, closed=False)
        self.notify("TOCEntry", (level, text, self.page - 1, key))


def split_report(markdown: str) -> tuple[str, list[str], list[str]]:
    lines = markdown.splitlines()
    if not lines or not lines[0].startswith("# "):
        raise ValueError("report must start with a level-one title")
    title = lines[0][2:].strip()
    abstract_index = lines.index("## Abstract")
    body_index = next(
        index for index in range(abstract_index + 1, len(lines)) if lines[index].startswith("## 1 ")
    )
    abstract_lines = lines[abstract_index + 1 : body_index]
    body_lines = lines[body_index:]
    return title, abstract_lines, body_lines


def title_page(
    title: str,
    author: str,
    report_date: str,
    styles: dict[str, ParagraphStyle],
) -> list:
    milestone, _, report_title = title.partition(":")
    return [
        Spacer(1, 23 * mm),
        Paragraph("The University of Manchester", styles["title_kicker"]),
        Paragraph("Department of Computer Science", styles["title_kicker"]),
        Spacer(1, 18 * mm),
        Paragraph("Project Report", styles["title_kicker"]),
        Spacer(1, 8 * mm),
        Paragraph(inline_markup(milestone, code_size=None), styles["title_kicker"]),
        Paragraph(inline_markup(report_title.strip(), code_size=None), styles["title"]),
        Spacer(1, 10 * mm),
        KeepTogether(
            [
                Paragraph(f"<b>Author:</b> {html.escape(author)}", styles["title_meta"]),
                Paragraph(f"<b>Date:</b> {html.escape(report_date)}", styles["title_meta"]),
            ]
        ),
        Spacer(1, 38 * mm),
        Paragraph(
            "Modern Linux KMD/UMD implementation and virtual-platform correctness validation",
            styles["title_meta"],
        ),
        PageBreak(),
    ]


def contents_page(styles: dict[str, ParagraphStyle]) -> list:
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle(
            "TOCLevel1",
            fontName=font_name("bold"),
            fontSize=10.5,
            leading=15,
            leftIndent=0,
            firstLineIndent=0,
            spaceBefore=4,
        ),
        ParagraphStyle(
            "TOCLevel2",
            fontName=font_name("regular"),
            fontSize=9.5,
            leading=13,
            leftIndent=13,
            firstLineIndent=0,
            spaceBefore=1,
        ),
    ]
    return [Paragraph("Contents", styles["toc_title"]), toc, PageBreak()]


def render(input_path: Path, output_path: Path, author: str, report_date: str) -> None:
    register_fonts()
    styles = build_styles()
    title, abstract_lines, body_lines = split_report(input_path.read_text(encoding="ascii"))

    story = title_page(title, author, report_date, styles)
    story.append(heading_flow("Abstract", 1, styles))
    story.extend(parse_blocks(abstract_lines, styles, page_break_headings=False))
    story.append(PageBreak())
    story.extend(contents_page(styles))
    story.extend(parse_blocks(body_lines, styles, page_break_headings=True))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = MilestoneDocTemplate(output_path, title, author)
    document.multiBuild(story)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Markdown report source")
    parser.add_argument("--output", type=Path, required=True, help="Destination PDF")
    parser.add_argument("--author", default="Berkant Bakisli")
    parser.add_argument("--date", default="10 July 2026")
    args = parser.parse_args()
    render(args.input, args.output, args.author, args.date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
