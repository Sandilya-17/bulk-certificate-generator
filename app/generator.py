"""The single predefined certificate template, rendered to PDF with ReportLab.

`render_certificate(path, data)` is the only thing the worker depends on, so the
template (or even the output format) can be swapped without touching job logic.
"""
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

PAGE_SIZE = landscape(A4)  # 842 x 595 pt

NAVY = HexColor("#1F2A44")
GOLD = HexColor("#B8892B")
CREAM = HexColor("#FBF8F1")
GREY = HexColor("#555B66")


@dataclass(frozen=True)
class CertificateData:
    recipient_name: str
    course_name: str
    title: str
    issuer_name: str
    issue_date: date
    verification_code: str
    grade: str | None = None


def _fit_font_size(text: str, font: str, max_size: float, min_size: float, max_width: float) -> float:
    """Shrink the font until the text fits on one line (long names / course titles)."""
    size = max_size
    while size > min_size and stringWidth(text, font, size) > max_width:
        size -= 1
    return size


def render_certificate(output_path: Path, data: CertificateData) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write to a temp file and rename, so a crash never leaves a half-written PDF
    # at the final path.
    tmp_path = output_path.with_name(output_path.name + ".tmp")
    width, height = PAGE_SIZE
    cx = width / 2
    text_width = width - 160

    c = canvas.Canvas(str(tmp_path), pagesize=PAGE_SIZE)
    c.setTitle(f"{data.title} - {data.recipient_name}")
    c.setAuthor(data.issuer_name)
    c.setSubject(data.course_name)

    # Background and double border
    c.setFillColor(CREAM)
    c.rect(0, 0, width, height, fill=1, stroke=0)
    c.setStrokeColor(NAVY)
    c.setLineWidth(6)
    c.rect(20, 20, width - 40, height - 40)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1.5)
    c.rect(32, 32, width - 64, height - 64)

    # Title
    c.setFillColor(NAVY)
    title = data.title.upper()
    c.setFont("Helvetica-Bold", _fit_font_size(title, "Helvetica-Bold", 34, 16, text_width))
    c.drawCentredString(cx, height - 115, title)
    c.setStrokeColor(GOLD)
    c.setLineWidth(2)
    c.line(cx - 90, height - 132, cx + 90, height - 132)

    # Body
    c.setFillColor(GREY)
    c.setFont("Helvetica", 14)
    c.drawCentredString(cx, height - 185, "This is to certify that")

    c.setFillColor(NAVY)
    name_font = "Times-BoldItalic"
    c.setFont(name_font, _fit_font_size(data.recipient_name, name_font, 42, 12, text_width))
    c.drawCentredString(cx, height - 245, data.recipient_name)
    c.setStrokeColor(GREY)
    c.setLineWidth(0.75)
    c.line(cx - 220, height - 260, cx + 220, height - 260)

    c.setFillColor(GREY)
    c.setFont("Helvetica", 14)
    c.drawCentredString(cx, height - 295, "has successfully completed")

    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", _fit_font_size(data.course_name, "Helvetica-Bold", 24, 10, text_width))
    c.drawCentredString(cx, height - 335, data.course_name)

    if data.grade:
        c.setFillColor(GREY)
        c.setFont("Helvetica-Oblique", 13)
        c.drawCentredString(cx, height - 368, f"with grade: {data.grade}")

    # Date (left) and issuer (right) "signature" blocks
    c.setStrokeColor(NAVY)
    c.setLineWidth(1)
    for x, value, label in (
        (width * 0.25, data.issue_date.strftime("%d %B %Y"), "Date of Issue"),
        (width * 0.75, data.issuer_name, "Issued By"),
    ):
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", _fit_font_size(value, "Helvetica-Bold", 13, 7, 220))
        c.drawCentredString(x, 128, value)
        c.line(x - 110, 120, x + 110, 120)
        c.setFillColor(GREY)
        c.setFont("Helvetica", 10)
        c.drawCentredString(x, 106, label)

    # Footer with verification code
    c.setFillColor(GREY)
    c.setFont("Helvetica", 8)
    c.drawCentredString(cx, 48, f"Certificate ID: {data.verification_code}")

    c.showPage()
    c.save()
    os.replace(tmp_path, output_path)
