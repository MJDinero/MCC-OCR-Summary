"""PDF generation service with pluggable backend.

Default implementation uses reportlab *if available*, else falls back to a
very simple pure-Python minimal PDF generator (sufficient for tests) so the
module works without optional dependencies during early development.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence, Dict
from io import BytesIO
import logging
import math

from src.errors import PDFGenerationError

_LOG = logging.getLogger("pdf_writer")

try:  # pragma: no cover - optional metrics
    from prometheus_client import Counter  # type: ignore

    _PDF_CALLS = Counter(
        "pdf_writer_calls_total", "Total PDF generation calls", ["status"]
    )
except Exception:  # pragma: no cover
    _PDF_CALLS = None  # type: ignore


class PDFBackend(Protocol):  # pragma: no cover - interface only
    def build(
        self, title: str, sections: Sequence[tuple[str, str]]
    ) -> bytes:  # noqa: D401
        ...


class ReportLabBackend:
    def build(
        self, title: str, sections: Sequence[tuple[str, str]]
    ) -> bytes:  # pragma: no cover - depends on external lib
        try:  # noqa: WPS501
            from reportlab.lib.pagesizes import LETTER  # type: ignore
            from reportlab.lib.units import inch  # type: ignore
            from reportlab.lib.styles import (  # type: ignore
                ParagraphStyle,
                getSampleStyleSheet,
            )
            from reportlab.platypus import (  # type: ignore
                SimpleDocTemplate,
                Paragraph,
                Spacer,
                ListFlowable,
                ListItem,
                PageBreak,
            )
        except Exception as exc:  # pragma: no cover
            raise PDFGenerationError(f"reportlab not installed: {exc}") from exc
        buf = BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=LETTER,
            leftMargin=0.75 * inch,
            rightMargin=0.75 * inch,
            topMargin=0.85 * inch,
            bottomMargin=0.85 * inch,
        )
        stylesheet = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "ReportTitle",
            parent=stylesheet["Title"],
            fontName="Helvetica-Bold",
            fontSize=16,
            leading=20,
            spaceAfter=18,
        )
        heading_style = ParagraphStyle(
            "SectionHeading",
            parent=stylesheet["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=16,
            spaceBefore=6,
            spaceAfter=8,
        )
        body_style = ParagraphStyle(
            "BodyText",
            parent=stylesheet["BodyText"],
            fontName="Helvetica",
            fontSize=11,
            leading=14,
            spaceAfter=6,
        )
        bullet_style = ParagraphStyle(
            "BulletText",
            parent=body_style,
            leftIndent=18,
            bulletIndent=10,
            spaceAfter=4,
        )

        flowables = [Paragraph(title, title_style), Spacer(1, 12)]
        max_lines_per_page = 46
        current_lines = 4  # approximate lines consumed by title block

        for heading, body in sections:
            paragraphs, bullets = _parse_section_body(body)
            estimated_lines = 2 + _estimate_section_lines(paragraphs, bullets)
            if current_lines + estimated_lines > max_lines_per_page:
                flowables.append(PageBreak())
                current_lines = 0

            flowables.append(Paragraph(heading, heading_style))
            current_lines += 2

            if bullets:
                list_items = [
                    ListItem(
                        Paragraph(item, bullet_style),
                        value="•",
                    )
                    for item in bullets
                ]
                flowables.append(
                    ListFlowable(
                        list_items,
                        bulletType="bullet",
                        start="bullet",
                        leftIndent=12,
                        bulletFontName="Helvetica",
                        bulletFontSize=11,
                    )
                )
                current_lines += len(bullets) + 1
                if paragraphs:
                    for para in paragraphs:
                        flowables.append(Paragraph(para, body_style))
                        approx_lines = max(1, math.ceil(len(para) / 90))
                        current_lines += approx_lines
            else:
                content_paragraphs = paragraphs or ["N/A"]
                for para in content_paragraphs:
                    flowables.append(Paragraph(para, body_style))
                    approx_lines = max(1, math.ceil(len(para) / 90))
                    current_lines += approx_lines

            flowables.append(Spacer(1, 10))
            current_lines += 1

        try:
            doc.build(flowables)
        except Exception as exc:  # pragma: no cover - rendering failures
            raise PDFGenerationError(f"Failed to render PDF via reportlab: {exc}") from exc
        return buf.getvalue()


def _wrap_text(txt: str, width: int) -> list[str]:
    words = txt.split()
    lines: list[str] = []
    line: list[str] = []
    ln_len = 0
    for w in words:
        if ln_len + len(w) + (1 if line else 0) > width:
            lines.append(" ".join(line))
            line = [w]
            ln_len = len(w)
        else:
            line.append(w)
            ln_len += len(w) + (1 if line[:-1] else 0)
    if line:
        lines.append(" ".join(line))
    return lines or [""]


def _parse_section_body(body: str) -> tuple[list[str], list[str]]:
    raw_lines = [(line or "").strip() for line in (body or "").splitlines()]
    paragraphs: list[str] = []
    bullets: list[str] = []
    buffer: list[str] = []

    def _flush_buffer() -> None:
        if buffer:
            paragraphs.append(" ".join(buffer).strip())
            buffer.clear()

    for raw in raw_lines:
        if not raw:
            _flush_buffer()
            continue
        bullet_candidate = raw.lstrip()
        if bullet_candidate.startswith(("•", "-", "*")):
            text = bullet_candidate.lstrip("•-* ").strip()
            if text:
                bullets.append(text)
            continue
        buffer.append(raw.strip())
    _flush_buffer()

    if bullets and not paragraphs:
        return [], bullets
    return paragraphs or (["N/A"] if not bullets else []), bullets


def _estimate_section_lines(paragraphs: list[str], bullets: list[str]) -> int:
    if bullets:
        return len(bullets) + 2
    if not paragraphs:
        return 1
    lines = 0
    for para in paragraphs:
        approx = max(1, math.ceil(len(para) / 90))
        lines += approx
    return lines


class MinimalPDFBackend:
    """Tiny fallback PDF builder (not full spec) adequate for tests.

    Produces a single-page textual PDF using plain text objects. Not suitable
    for production rendering sophistication but keeps tests self-contained.
    """

    def build(self, title: str, sections: Sequence[tuple[str, str]]) -> bytes:
        try:
            lines = [title]
            for heading, body in sections:
                lines.append(heading)
                lines.extend(_wrap_text(body, 100))
            # Minimalistic PDF creation
            content_stream = "\n".join(lines)
            pdf_bytes = _simple_pdf(content_stream)
            return pdf_bytes
        except Exception as exc:  # pragma: no cover
            raise PDFGenerationError(f"Failed to build minimal PDF: {exc}") from exc


def _simple_pdf(text: str) -> bytes:
    # This is a very naive PDF writer for testing; ensures %PDF header
    # Reference: simplest possible PDF with one page & one text object.
    lines = text.splitlines() or [text]
    escaped_lines = []
    for line in lines:
        escaped = (
            line.replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
        )
        escaped_lines.append(escaped)
    content_ops = [
        "BT",
        "/F1 12 Tf",
        "1 0 0 1 72 720 Tm",
        "14 TL",
    ]
    for escaped in escaped_lines:
        content_ops.append(f"({escaped}) Tj")
        content_ops.append("T*")
    content_ops.append("ET")
    stream = "\n".join(content_ops)
    objects = []
    # 1: Catalog
    objects.append("1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj")
    # 2: Pages
    objects.append("2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj")
    # 3: Page
    objects.append(
        "3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources<< /Font<< /F1 5 0 R >> >> >>endobj"
    )
    # 4: Content
    objects.append(
        f"4 0 obj<< /Length {len(stream)} >>stream\n{stream}\nendstream endobj"
    )
    # 5: Font
    objects.append(
        "5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj"
    )
    xref_positions = []
    pdf = ["%PDF-1.4"]
    for obj in objects:
        xref_positions.append(sum(len(p) + 1 for p in pdf))
        pdf.append(obj)
    xref_start = sum(len(p) + 1 for p in pdf)
    pdf.append("xref")
    pdf.append(f"0 {len(objects)+1}")
    pdf.append("0000000000 65535 f ")
    for pos in xref_positions:
        pdf.append(f"{pos:010d} 00000 n ")
    pdf.append("trailer<< /Size 6 /Root 1 0 R >>")
    pdf.append("startxref")
    pdf.append(str(xref_start))
    pdf.append("%%EOF")
    return ("\n".join(pdf)).encode("utf-8")


@dataclass
class PDFWriter:
    backend: PDFBackend
    title: str = "Document Summary"

    def build(
        self,
        summary_or_title: Dict[str, str] | str,
        sections: Sequence[tuple[str, str]] | None = None,
    ) -> bytes:
        if sections is not None:
            title = str(summary_or_title or "").strip() or self.title
            sections_seq = []
            for heading, body in sections:
                heading_text = str(heading or "").strip() or "Section"
                body_text = str(body or "").strip() or "N/A"
                sections_seq.append((heading_text, body_text))
            if not sections_seq:
                raise PDFGenerationError("Summary structure empty")
        else:
            title = self.title
            if isinstance(summary_or_title, str):
                summary_text = summary_or_title.strip()
                if not summary_text:
                    raise PDFGenerationError("Summary text empty")
                sections_seq = [("Summary", summary_text)]
            else:
                summary = summary_or_title
                if not summary:
                    raise PDFGenerationError("Summary structure empty")
                order = [
                    "Patient Information",
                    "Medical Summary",
                    "Billing Highlights",
                    "Legal / Notes",
                ]
                sections_seq = []
                for key in order:
                    if key in summary:
                        val = (summary[key] or "").strip() or "N/A"
                        sections_seq.append((key, val))
                for k in sorted(
                    k for k in summary.keys() if k not in {o for o, _ in sections_seq}
                ):
                    sections_seq.append((k, (summary[k] or "").strip()))
                diag_list = [
                    s
                    for s in (summary.get("_diagnoses_list", "").splitlines())
                    if s.strip()
                ]
                prov_list = [
                    s
                    for s in (summary.get("_providers_list", "").splitlines())
                    if s.strip()
                ]
                med_list = [
                    s
                    for s in (summary.get("_medications_list", "").splitlines())
                    if s.strip()
                ]
                any_lists = any([diag_list, prov_list, med_list])
                if any_lists:
                    sections_seq.append(
                        (
                            "Structured Indices",
                            "=" * 48,
                        )
                    )

                    def _fmt_block(title: str, items: list[str]) -> str:
                        if not items:
                            return f"{title}:\nN/A"
                        return f"{title}:\n" + "\n".join(f"• {i}" for i in items)

                    sections_seq.append(("Diagnoses", _fmt_block("Diagnoses", diag_list)))
                    sections_seq.append(("Providers", _fmt_block("Providers", prov_list)))
                    sections_seq.append(
                        (
                            "Medications",
                            _fmt_block("Medications / Prescriptions", med_list),
                        )
                    )
        _LOG.info(
            "pdf_writer_started",
            extra={
                "sections": len(sections_seq),
                "structured_indices": bool(
                    sum(
                        1
                        for heading, _ in sections_seq
                        if heading in {"Diagnoses", "Providers", "Medications"}
                    )
                ),
            },
        )
        try:
            result = self.backend.build(title, sections_seq)
            if _PDF_CALLS:
                _PDF_CALLS.labels(status="success").inc()
            _LOG.info(
                "pdf_writer_complete",
                extra={
                    "bytes": len(result),
                    "sections": len(sections_seq),
                },
            )
            return result
        except PDFGenerationError:
            if _PDF_CALLS:
                _PDF_CALLS.labels(status="error").inc()
            raise
        except Exception as exc:
            if _PDF_CALLS:
                _PDF_CALLS.labels(status="unexpected").inc()
            raise PDFGenerationError(f"Failed generating PDF: {exc}") from exc


def write_summary_pdf(summary: str, output_path: str) -> None:
    """Backward compatible helper using MinimalPDFBackend."""
    writer = PDFWriter(MinimalPDFBackend())
    data = writer.build(summary)
    with open(output_path, "wb") as f:  # noqa: P103
        f.write(data)


__all__ = [
    "PDFWriter",
    "PDFBackend",
    "ReportLabBackend",
    "MinimalPDFBackend",
    "write_summary_pdf",
]
