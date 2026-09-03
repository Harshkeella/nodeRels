"""Professional, paginated output using embedded fonts and measured flowables."""
import io
import threading
from pathlib import Path
from xml.sax.saxutils import escape

from matplotlib import get_data_path
from matplotlib.font_manager import FontProperties
from matplotlib.mathtext import math_to_image
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image

from .content import Document

_lock = threading.Lock()  # matplotlib font/math caches are process-global


def equation_png(text: str) -> bytes:
    with _lock:
        out = io.BytesIO()
        try:
            math_to_image("$" + text.strip().strip("$") + "$", out,
                          prop=FontProperties(size=22), dpi=180, format="png", color="#182437")
        except (ValueError, RuntimeError) as exc:
            raise ValueError("Unsupported formula notation. Use standard LaTeX math commands.") from exc
        return out.getvalue()


def render_pdf(document: Document, path: str | Path) -> None:
    fonts = Path(get_data_path()) / "fonts" / "ttf"
    with _lock:
        for name, file in (("Artifact", "DejaVuSans.ttf"), ("ArtifactBold", "DejaVuSans-Bold.ttf"), ("ArtifactMono", "DejaVuSansMono.ttf")):
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(fonts / file)))
    body = ParagraphStyle("Body", fontName="Artifact", fontSize=10.5, leading=16,
                          textColor=colors.HexColor("#253247"), spaceAfter=10, alignment=TA_LEFT,
                          splitLongWords=True)
    heading = ParagraphStyle("Heading", parent=body, fontName="ArtifactBold", fontSize=16,
                             leading=21, spaceBefore=18, spaceAfter=10, keepWithNext=True)
    title = ParagraphStyle("Title", parent=heading, fontSize=26, leading=33, spaceBefore=0, spaceAfter=20)
    subheading = ParagraphStyle("Subheading", parent=heading, fontSize=12.5, leading=17, spaceBefore=12)
    cell = ParagraphStyle("Cell", parent=body, fontSize=9, leading=13, spaceAfter=0)
    code = ParagraphStyle("Code", parent=body, fontName="ArtifactMono", fontSize=9, leading=14,
                          backColor=colors.HexColor("#F1F4F8"), borderPadding=8)
    width = 595.28 - 108
    flow = [Paragraph(escape(document.title), title)]
    for index, section in enumerate(document.sections):
        if index or section.title != document.title:
            flow.append(Paragraph(escape(section.title), heading if section.level == 2 else subheading))
        for block in section.blocks:
            if block.kind == "table":
                rows = [[Paragraph(escape(c), cell) for c in row] for row in block.rows]
                table = Table(rows, colWidths=[width / len(rows[0])] * len(rows[0]), repeatRows=1,
                              hAlign="LEFT", splitByRow=1, splitInRow=1)
                table.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF5")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LINEBELOW", (0, 0), (-1, 0), 1, colors.HexColor("#8192AA")),
                    ("LINEBELOW", (0, 1), (-1, -1), .4, colors.HexColor("#DCE3EC")),
                    ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]))
                flow.extend([table, Spacer(1, 12)])
            elif block.kind == "equation":
                try:
                    raw = equation_png(block.text)
                except ValueError:
                    flow.append(Paragraph(escape(block.text).replace("\n", "<br/>"), code))
                    continue
                img = Image(io.BytesIO(raw))
                scale = min(width / img.imageWidth, 90 / img.imageHeight, .5)
                img.drawWidth, img.drawHeight = img.imageWidth * scale, img.imageHeight * scale
                img.hAlign = "LEFT"
                flow.extend([img, Spacer(1, 12)])
            else:
                flow.append(Paragraph(escape(block.text).replace("\n", "<br/>"), code if block.kind == "code" else body))
    if document.sources:
        flow.append(Paragraph("Sources", heading))
        for index, source in enumerate(document.sources, 1):
            flow.append(Paragraph(escape(f"{index}. {source}"), cell))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#DCE3EC"))
        canvas.line(54, 44, 541, 44)
        canvas.setFont("Artifact", 8)
        canvas.setFillColor(colors.HexColor("#56657B"))
        canvas.drawString(54, 30, "nodeRels · Knowledge brief")
        canvas.drawRightString(541, 30, str(doc.page))
        canvas.restoreState()

    SimpleDocTemplate(str(path), pagesize=(595.28, 841.89), leftMargin=54, rightMargin=54,
                      topMargin=48, bottomMargin=60, title=document.title, author="nodeRels").build(
                          flow, onFirstPage=footer, onLaterPages=footer)
