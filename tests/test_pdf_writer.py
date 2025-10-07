import os
import tempfile
import pytest

from src.services.pdf_writer import PDFWriter, MinimalPDFBackend, write_summary_pdf
from src.errors import PDFGenerationError


def test_minimal_backend_bytes():
    backend = MinimalPDFBackend()
    writer = PDFWriter(backend, title="T")
    pdf_bytes = writer.build("Some summary text")
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 50


def test_empty_summary_error():
    backend = MinimalPDFBackend()
    writer = PDFWriter(backend)
    with pytest.raises(PDFGenerationError):
        writer.build("   ")


def test_write_summary_pdf_helper():
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        write_summary_pdf("Summary body", path)
        with open(path, "rb") as f:
            data = f.read()
        assert data.startswith(b"%PDF-")
    finally:
        os.remove(path)
