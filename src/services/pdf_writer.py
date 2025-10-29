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
import re

from src.errors import PDFGenerationError

_LOG = logging.getLogger("pdf_writer")

try:  # pragma: no cover - optional metrics
    from prometheus_client import Counter  # type: ignore
    _PDF_CALLS = Counter("pdf_writer_calls_total", "Total PDF generation calls", ["status"])
except Exception:  # pragma: no cover
    _PDF_CALLS = None  # type: ignore


_ASCII_BULLET_TRANSLATION = str.maketrans({
    "•": "-",
    "‣": "-",
    "▪": "-",
    "◦": "-",
    "●": "-",
    "○": "-",
    "∙": "-",
    "‒": "-",
    "–": "-",
    "—": "-",
    "―": "-",
    "−": "-",
})


_PAGE_WIDTH = 612
_PAGE_HEIGHT = 792
_LEFT_MARGIN = 72
_RIGHT_MARGIN = 72
_TOP_MARGIN = 72
_BOTTOM_MARGIN = 72
_TEXT_LEADING = 14
_LINES_PER_PAGE = max(1, (_PAGE_HEIGHT - _TOP_MARGIN - _BOTTOM_MARGIN) // _TEXT_LEADING)


def _normalise_ascii(text: str) -> str:
    """Coerce Unicode bullets/dashes to ASCII hyphen bullets for predictable extraction."""

    if not text:
        return ""
    normalised = text.translate(_ASCII_BULLET_TRANSLATION)
    normalised = re.sub(r"(?m)^\s*-\s*", "- ", normalised)
    normalised = re.sub(r"[ \t]{2,}", " ", normalised)
    return normalised


class PDFBackend(Protocol):  # pragma: no cover - interface only
    def build(self, title: str, sections: Sequence[tuple[str, str]]) -> bytes:  # noqa: D401
        ...


class ReportLabBackend:
    def build(self, title: str, sections: Sequence[tuple[str, str]]) -> bytes:  # pragma: no cover - depends on external lib
        try:  # noqa: WPS501
            from reportlab.lib.pagesizes import LETTER  # type: ignore
            from reportlab.pdfgen import canvas  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise PDFGenerationError(f"reportlab not installed: {exc}") from exc
        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=LETTER)
        _width, height = LETTER
        y = height - 72
        c.setFont("Helvetica-Bold", 16)
        c.drawString(72, y, title)
        y -= 36
        c.setFont("Helvetica", 11)
        for heading, body in sections:
            if y < 100:
                c.showPage()
                y = height - 72
                c.setFont("Helvetica", 11)
            c.setFont("Helvetica-Bold", 12)
            c.drawString(72, y, heading)
            y -= 18
            c.setFont("Helvetica", 11)
            for line in _wrap_text(body, 90):
                if y < 72:
                    c.showPage()
                    y = height - 72
                    c.setFont("Helvetica", 11)
                c.drawString(72, y, line)
                y -= 14
            y -= 10
        c.showPage()
        c.save()
        return buf.getvalue()


def _wrap_text(txt: str, width: int) -> list[str]:
    if not txt:
        return [""]
    paragraphs = txt.splitlines() or [txt]
    lines: list[str] = []
    for paragraph in paragraphs:
        normalised = _normalise_ascii(paragraph).strip()
        if not normalised:
            lines.append("")
            continue
        words = normalised.split()
        current: list[str] = []
        line_len = 0
        for word in words:
            projected_len = line_len + len(word) + (1 if current else 0)
            if projected_len > width and current:
                lines.append(" ".join(current))
                current = [word]
                line_len = len(word)
            else:
                current.append(word)
                line_len = projected_len
        if current:
            lines.append(" ".join(current))
    # Trim leading/trailing empty lines introduced by formatting
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    return lines or [""]


class MinimalPDFBackend:
    """Tiny fallback PDF builder (not full spec) adequate for tests.

    Produces a single-page textual PDF using plain text objects. Not suitable
    for production rendering sophistication but keeps tests self-contained.
    """
    def build(self, title: str, sections: Sequence[tuple[str, str]]) -> bytes:
        try:
            lines = [_normalise_ascii(title)]
            for heading, body in sections:
                lines.append(_normalise_ascii(heading))
                wrapped = _wrap_text(body, 100)
                if not wrapped:
                    wrapped = [""]
                lines.extend(wrapped)
            if not lines:
                lines = [""]
            pdf_bytes = _simple_pdf(lines)
            return pdf_bytes
        except Exception as exc:  # pragma: no cover
            raise PDFGenerationError(f"Failed to build minimal PDF: {exc}") from exc


def _simple_pdf(lines: Sequence[str]) -> bytes:
    # This is a very naive PDF writer for testing; ensures %PDF header
    # Reference: simplest possible PDF with one page & one text object.
    def _escape(segment: str) -> str:
        return segment.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    if not lines:
        lines = [""]

    line_chunks: list[list[str]] = []
    for start in range(0, len(lines), _LINES_PER_PAGE):
        chunk = list(lines[start : start + _LINES_PER_PAGE])
        if not chunk:
            chunk = [""]
        line_chunks.append(chunk)
    if not line_chunks:
        line_chunks = [[lines[0]]]

    objects: list[str] = []
    page_count = len(line_chunks)
    page_object_numbers = [3 + idx * 2 for idx in range(page_count)]
    content_object_numbers = [num + 1 for num in page_object_numbers]
    font_object_number = 3 + 2 * page_count

    objects.append("1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj")

    kids = " ".join(f"{num} 0 R" for num in page_object_numbers)
    objects.append(f"2 0 obj<< /Type /Pages /Kids [{kids}] /Count {page_count} >>endobj")

    for idx, chunk in enumerate(line_chunks):
        page_obj_num = page_object_numbers[idx]
        content_obj_num = content_object_numbers[idx]
        content_ops = [
            f"BT /F1 12 Tf {_LEFT_MARGIN} {_PAGE_HEIGHT - _TOP_MARGIN} Td",
            f"{_TEXT_LEADING} TL",
        ]
        for line_idx, line in enumerate(chunk):
            escaped = _escape(line or "")
            if line_idx == 0:
                content_ops.append(f"({escaped}) Tj")
            else:
                content_ops.append(f"T* ({escaped}) Tj")
        content_ops.append("ET")
        stream = "\n".join(content_ops)
        stream_bytes = stream.encode("utf-8")

        objects.append(
            f"{page_obj_num} 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PAGE_WIDTH} {_PAGE_HEIGHT}] "
            f"/Contents {content_obj_num} 0 R /Resources<< /Font<< /F1 {font_object_number} 0 R >> >> >>endobj"
        )
        objects.append(
            f"{content_obj_num} 0 obj<< /Length {len(stream_bytes)} >>stream\n{stream}\nendstream endobj"
        )

    objects.append(
        f"{font_object_number} 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj"
    )

    xref_positions = []
    pdf_parts = ["%PDF-1.4"]
    for obj in objects:
        xref_positions.append(sum(len(part) + 1 for part in pdf_parts))
        pdf_parts.append(obj)
    xref_start = sum(len(part) + 1 for part in pdf_parts)
    pdf_parts.append("xref")
    pdf_parts.append(f"0 {len(objects) + 1}")
    pdf_parts.append("0000000000 65535 f ")
    for pos in xref_positions:
        pdf_parts.append(f"{pos:010d} 00000 n ")
    pdf_parts.append(f"trailer<< /Size {len(objects) + 1} /Root 1 0 R >>")
    pdf_parts.append("startxref")
    pdf_parts.append(str(xref_start))
    pdf_parts.append("%%EOF")
    return "\n".join(pdf_parts).encode("utf-8")

@dataclass
class PDFWriter:
    backend: PDFBackend
    title: str = "Document Summary"

    def build(self, summary: Dict[str, str] | str) -> bytes:
        # Accept legacy string but prefer dict
        if isinstance(summary, str):
            if not summary.strip():
                raise PDFGenerationError("Summary text empty")
            sections_seq = [("Summary", summary.strip())]
        else:
            if not summary:
                raise PDFGenerationError("Summary structure empty")
            # Preserve deterministic ordering
            order = ["Patient Information", "Medical Summary", "Billing Highlights", "Legal / Notes"]
            sections_seq = []
            used_keys = set()
            for key in order:
                if key in summary:
                    val = (summary[key] or '').strip() or 'N/A'
                    sections_seq.append((key, val))
                    used_keys.add(key)
            # Add any extra keys deterministically
            extra_keys = sorted(
                k for k in summary.keys()
                if k not in used_keys and not k.startswith("_")
            )
            for k in extra_keys:
                sections_seq.append((k, (summary[k] or '').strip()))
        # Detect structured lists via side-channel keys injected by Summariser
        if isinstance(summary, dict):
            diag_list = [s for s in (summary.get("_diagnoses_list", "").splitlines()) if s.strip()]
            prov_list = [s for s in (summary.get("_providers_list", "").splitlines()) if s.strip()]
            med_list = [s for s in (summary.get("_medications_list", "").splitlines()) if s.strip()]
            any_lists = any([diag_list, prov_list, med_list])
            if any_lists:
                structured_intro = "\n".join(
                    [
                        "-" * 38,
                        "Diagnoses, Providers, and Medications captured below.",
                        "-" * 38,
                    ]
                )
                sections_seq.append(("Structured Indices", structured_intro))

                def _fmt_block(title: str, items: list[str]) -> str:
                    normalised_items = [_normalise_ascii(item.strip()) for item in items if item.strip()]
                    if not normalised_items:
                        return f"{title}:\nN/A"
                    return f"{title}:\n" + "\n".join(f"- {i}" for i in normalised_items)

                sections_seq.append(("Diagnoses", _fmt_block("Diagnoses", diag_list)))
                sections_seq.append(("Providers", _fmt_block("Providers", prov_list)))
                sections_seq.append(("Medications", _fmt_block("Medications / Prescriptions", med_list)))
        sections_seq = [
            (_normalise_ascii(heading), _normalise_ascii(body if body else ""))
            for heading, body in sections_seq
        ]
        title = _normalise_ascii(self.title)
        _LOG.info(
            "pdf_writer_started",
            extra={
                "sections": len(sections_seq),
                "structured_indices": bool(sum(1 for heading, _ in sections_seq if heading in {"Diagnoses", "Providers", "Medications"})),
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
