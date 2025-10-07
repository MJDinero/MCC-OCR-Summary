import os
import tempfile
import pytest

from src.utils.docai_request_builder import build_docai_request, MAX_PDF_BYTES
from src.errors import ValidationError

# Minimal valid PDF bytes (header + body + EOF)
VALID_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"  # tiny


def _write_pdf(data: bytes, suffix: str = ".pdf") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


def test_build_request_from_path_success():
    path = _write_pdf(VALID_PDF)
    name, req = build_docai_request(path, "proj", "us", "proc")
    assert name.endswith("/processors/proc")
    assert req["raw_document"]["content"].startswith(b"%PDF-")


def test_build_request_from_bytes_success():
    name, req = build_docai_request(VALID_PDF, "p", "us", "x", filename="file.pdf")
    assert req["raw_document"]["mime_type"] == "application/pdf"


def test_invalid_extension_path():
    path = _write_pdf(VALID_PDF, suffix=".txt")
    with pytest.raises(ValidationError):
        build_docai_request(path, "p", "us", "x")


def test_empty_bytes():
    with pytest.raises(ValidationError):
        build_docai_request(b"", "p", "us", "x", filename="f.pdf")


def test_oversize_bytes():
    big = VALID_PDF + b"0" * (MAX_PDF_BYTES + 1)
    with pytest.raises(ValidationError):
        build_docai_request(big, "p", "us", "x", filename="f.pdf")


def test_missing_pdf_magic():
    not_pdf = b"HELLO WORLD" * 10
    with pytest.raises(ValidationError):
        build_docai_request(not_pdf, "p", "us", "x", filename="f.pdf")
