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
            # Add any extra keys deterministically
            for k in sorted(
                k for k in summary.keys() if k not in {o for o, _ in sections_seq}
            ):
                sections_seq.append((k, (summary[k] or "").strip()))
        # Detect structured lists via side-channel keys injected by Summariser
        if isinstance(summary, dict):
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
                sections_seq.append(("Structured Indices", "=" * 48))

                def _fmt_block(title: str, items: list[str]) -> str:
                    if not items:
                        return "N/A"
                    return "\n".join(f"• {i}" for i in items)

                sections_seq.append(("Diagnoses", _fmt_block("Diagnoses", diag_list)))
                sections_seq.append(("Healthcare Providers", _fmt_block("Providers", prov_list)))
                sections_seq.append(
                    (
                        "Medications / Prescriptions",
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
            result = self.backend.build(self.title, sections_seq)
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
