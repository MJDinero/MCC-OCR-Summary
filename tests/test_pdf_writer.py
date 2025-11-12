from __future__ import annotations

from io import BytesIO

import pytest
from pypdf import PdfReader

from src.errors import PDFGenerationError
from src.services.pdf_writer import PDFWriter, ReportLabBackend


def _extract_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    contents: list[str] = []
    for page in reader.pages:
        extracted = page.extract_text() or ""
        contents.append(extracted)
    return "\n".join(contents)


def test_reportlab_backend_emits_pdf():
    writer = PDFWriter(ReportLabBackend(), title="T")
    sections = [
        ("Intro Overview", "Patient evaluated in clinic."),
        ("Key Points", "- Key item one\n- Key item two"),
    ]
    pdf_bytes = writer.build("Clinical Summary", sections)
    assert pdf_bytes.startswith(b"%PDF-")
    text = _extract_text(pdf_bytes)
    assert "Clinical Summary" in text
    assert "- Key item one" in text


def test_empty_sections_error():
    writer = PDFWriter(ReportLabBackend())
    with pytest.raises(PDFGenerationError):
        writer.build("Title", [])
