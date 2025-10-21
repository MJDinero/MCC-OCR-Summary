from __future__ import annotations

import io

from PyPDF2 import PdfReader, PdfWriter

from src.services.docai_helper import _split_pdf_bytes


def _make_pdf(page_count: int) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=72, height=72)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_split_pdf_bytes_creates_expected_chunks() -> None:
    pdf_bytes = _make_pdf(63)
    parts = _split_pdf_bytes(pdf_bytes, max_pages=25)
    assert len(parts) == 3
    reader_counts = []
    for chunk in parts:
        reader_counts.append(len(PdfReader(io.BytesIO(chunk)).pages))
    assert reader_counts == [25, 25, 13]
