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


def test_minimal_backend_preserves_multiline_sections():
    backend = MinimalPDFBackend()
    writer = PDFWriter(backend, title="Summary")
    summary = {
        "Patient Information": "Not provided",
        "Medical Summary": "Intro Overview:\nLine one\n\nKey Points:\n- Item A\n- Item B",
    }
    pdf_bytes = writer.build(summary)
    # Expect multiple text commands with explicit line breaks to aid downstream text extraction.
    assert pdf_bytes.count(b"Tj") >= 4
    assert b"T*" in pdf_bytes


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


def test_pdf_writer_normalises_unicode_bullets():
    backend = MinimalPDFBackend()
    writer = PDFWriter(backend, title="Summary")
    summary = {
        "Patient Information": "Not provided",
        "Medical Summary": "Intro Overview:\n• Initial observation\n\nKey Points:\n• Follow-up pending",
        "Billing Highlights": "N/A",
        "Legal / Notes": "N/A",
    }
    pdf_bytes = writer.build(summary)
    text = pdf_bytes.decode("utf-8", errors="ignore")
    assert "•" not in text
    assert "- Initial observation" in text


def test_minimal_backend_emits_ascii_only():
    backend = MinimalPDFBackend()
    writer = PDFWriter(backend, title="ASCII Test")
    summary = {
        "Patient Information": "Minimal ASCII payload",
        "Medical Summary": "Intro Overview:\nLine one\nLine two\nLine three",
    }
    pdf_bytes = writer.build(summary)
    assert pdf_bytes.startswith(b"%PDF-")
    assert all(byte < 128 for byte in pdf_bytes), "Minimal backend should emit ASCII-only PDF for deterministic parsing"


def test_paginates_long_text():
    backend = MinimalPDFBackend()
    writer = PDFWriter(backend, title="Paginator Test")
    long_lines = [f"Line {i:03d} extended narrative for pagination." for i in range(120)]
    summary = {
        "Patient Information": "Paginated patient info",
        "Medical Summary": "Intro Overview:\n" + "\n".join(long_lines),
    }
    pdf_bytes = writer.build(summary)

    assert pdf_bytes.count(b"/Type /Page ") == 3
    text = pdf_bytes.decode("utf-8", errors="ignore")
    assert "Line 000 extended narrative for pagination." in text
    assert "Line 119 extended narrative for pagination." in text


def test_ascii_and_one_tj_per_line():
    backend = MinimalPDFBackend()
    writer = PDFWriter(backend, title="Tj Counter")
    summary = "Intro overview line\n• Primary bullet\n• Secondary bullet"

    pdf_bytes = writer.build(summary)
    text = pdf_bytes.decode("utf-8", errors="ignore")

    assert "•" not in text
    assert "- Primary bullet" in text
    assert "- Secondary bullet" in text
    assert pdf_bytes.count(b") Tj") == 5
