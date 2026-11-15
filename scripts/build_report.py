"""Render docs/REPORT.md to docs/REPORT.pdf (reportlab, `uv sync --extra data`). Handles headings, paragraphs, bullets, tables, code, **bold**, `code`.

    uv run python -m scripts.build_report            # prints the page count; the assignment allows at most 10 pages (appendices unlimited)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parent.parent
SRC, OUT = ROOT / "docs" / "REPORT.md", ROOT / "docs" / "REPORT.pdf"


def inline(s: str) -> str:
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`(.+?)`", r"<font face='Courier' size='8'>\1</font>", s)
    return re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", s)


def build(text: str) -> list:
    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=8.6, leading=11.2, spaceAfter=3)
    cell = ParagraphStyle("c", parent=body, fontSize=7.4, leading=9, spaceAfter=0)
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontSize=15, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=11, spaceBefore=6, spaceAfter=2)
    out: list = []
    lines, i = text.splitlines(), 0
    para: list[str] = []

    def flush() -> None:
        if para:
            out.append(Paragraph(inline(" ".join(para)), body))
            para.clear()

    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            flush()
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            out.append(Preformatted("\n".join(lines[i + 1:j]), ParagraphStyle("p", fontName="Courier", fontSize=7, leading=8.5)))
            i = j + 1
            continue
        if ln.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                if not re.match(r"^\|[\s:|-]+\|$", lines[i]):
                    rows.append([Paragraph(inline(c.strip()), cell) for c in lines[i].strip().strip("|").split("|")])
                i += 1
            t = Table(rows, repeatRows=1, hAlign="LEFT")
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
            out += [t, Spacer(1, 4)]
            continue
        if ln.startswith("* ") or re.match(r"^\d+\. ", ln):
            flush()
            items, ordered = [], not ln.startswith("* ")
            while i < len(lines) and (lines[i].startswith(("* ", "  ")) or re.match(r"^\d+\. ", lines[i])):
                if lines[i].startswith("  ") and items:
                    items[-1] += " " + lines[i].strip()
                else:
                    items.append(re.sub(r"^(\* |\d+\. )", "", lines[i]))
                i += 1
            out.append(ListFlowable([ListItem(Paragraph(inline(x), body)) for x in items], bulletType="1" if ordered else "bullet", leftIndent=12,
                                    bulletFontSize=7))
            continue
        if ln.startswith("# "):
            flush()
            out.append(Paragraph(inline(ln[2:]), h1))
        elif ln.startswith("## "):
            flush()
            out.append(Paragraph(inline(ln[3:]), h2))
        elif not ln.strip():
            flush()
        else:
            para.append(ln.strip())
        i += 1
    flush()
    return out


def main() -> int:
    doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                            title="GigBid engineering design review")
    doc.build(build(SRC.read_text(encoding="utf-8")))
    try:
        from pypdf import PdfReader

        n = len(PdfReader(str(OUT)).pages)
        print(f"wrote {OUT} ({n} pages){'  OVER THE 10-PAGE LIMIT: move detail to appendices' if n > 10 else ''}")
    except Exception:  # noqa: BLE001
        print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
