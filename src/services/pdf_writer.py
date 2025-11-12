"""PDF generation service with a single, Platypus-based backend."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import logging
import math
from typing import Dict, Protocol, Sequence

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

            appended_content = False
            if bullets:
                for item in bullets:
                    flowables.append(Paragraph(f"- {item}", bullet_style))
                    current_lines += 1
                    appended_content = True
            content_paragraphs = paragraphs or ([] if appended_content else ["N/A"])
            for para in content_paragraphs:
                flowables.append(Paragraph(para, body_style))
                approx_lines = max(1, math.ceil(len(para) / 90))
                current_lines += approx_lines
                appended_content = True

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


@dataclass
class PDFWriter:
    backend: PDFBackend
    title: str = "Document Summary"

    def build(
        self,
        title: str,
        sections: Sequence[tuple[str, str]],
    ) -> bytes:
        title_text = str(title or "").strip() or self.title
        if not sections:
            raise PDFGenerationError("Summary structure empty")
        canonical_order: Dict[str, int] = {
            "Intro Overview": 0,
            "Key Points": 1,
            "Detailed Findings": 2,
            "Care Plan & Follow-Up": 3,
            "Diagnoses": 4,
            "Providers": 5,
            "Medications / Prescriptions": 6,
            "Patient Information": 7,
            "Medical Summary": 8,
            "Billing Highlights": 9,
            "Legal / Notes": 10,
        }
        sections_seq: list[tuple[str, str]] = []
        for heading, body in sections:
            heading_text = str(heading or "").strip() or "Section"
            body_lines = (body or "").splitlines()
            sanitised = "\n".join(line.rstrip() for line in body_lines).strip()
            sections_seq.append((heading_text, sanitised or "N/A"))
        indexed = list(enumerate(sections_seq))
        sections_seq = [
            section
            for _, section in sorted(
                indexed,
                key=lambda item: (
                    canonical_order.get(item[1][0], len(canonical_order) + item[0]),
                    item[0],
                ),
            )
        ]
        _LOG.info(
            "pdf_writer_started",
            extra={
                "sections": len(sections_seq),
                "structured_indices": bool(
                    sum(
                        1
                        for heading, _ in sections_seq
                        if heading
                        in {"Diagnoses", "Providers", "Medications / Prescriptions"}
                    )
                ),
            },
        )
        try:
            result = self.backend.build(title_text, sections_seq)
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


__all__ = [
    "PDFWriter",
    "PDFBackend",
    "ReportLabBackend",
]
