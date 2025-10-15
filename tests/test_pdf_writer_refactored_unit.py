from __future__ import annotations

import io
import json

import pytest

from src.errors import PDFGenerationError
from src.services import pdf_writer_refactored as pdf_mod


def test_wrap_text_splits_and_trims():
    lines = pdf_mod._wrap_text("First line\n\nSecond line", width=20)
    assert lines[0] == "First line"
    assert "" in lines  # preserves blank lines as empty string


def test_normalise_summary_builds_indices():
    summary = {
        "Patient Information": "Info",
        "Medical Summary": "Summary body",
        "_diagnoses_list": "Dx1\nDx2",
        "_providers_list": "Dr A\nDr B",
        "_medications_list": "Med1",
        "Extra Section": "Value",
    }
    sections, indices = pdf_mod._normalise_summary(summary)
    headings = [title for title, _ in sections]
    assert "Structured Indices" in headings
    assert indices["Diagnoses"] == ["Dx1", "Dx2"]
    assert ("Extra Section", "Value") in sections


def test_ensure_bytes_handles_variants():
    assert pdf_mod._ensure_bytes(b"abc") == b"abc"
    assert pdf_mod._ensure_bytes(bytearray(b"xyz")) == b"xyz"
    assert pdf_mod._ensure_bytes(memoryview(b"123")) == b"123"
    buffer = io.BytesIO(b"buffered")
    assert pdf_mod._ensure_bytes(buffer) == b"buffered"
    with pytest.raises(PDFGenerationError):
        pdf_mod._ensure_bytes("not-bytes")  # type: ignore[arg-type]


def test_parse_gcs_uri_validation():
    bucket, blob = pdf_mod._parse_gcs_uri("gs://bucket/path/to/file.pdf")
    assert bucket == "bucket"
    assert blob == "path/to/file.pdf"
    with pytest.raises(PDFGenerationError):
        pdf_mod._parse_gcs_uri("http://invalid/path")
    with pytest.raises(PDFGenerationError):
        pdf_mod._parse_gcs_uri("gs://bucket-only")


def test_load_summary_local(tmp_path):
    payload = {"Medical Summary": "Example summary", "_diagnoses_list": "Dx"}
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = pdf_mod._load_summary(summary_path)
    assert loaded["Medical Summary"] == "Example summary"


def test_load_summary_from_gcs(monkeypatch):
    payload = {"Medical Summary": "Remote summary", "_diagnoses_list": "Dx"}
    blob_bytes = json.dumps(payload).encode("utf-8")

    class _StubBlob:
        def __init__(self, data: bytes | None) -> None:
            self._data = data

        def download_as_bytes(self) -> bytes:
            if self._data is None:
                raise FileNotFoundError("missing")
            return self._data

    class _StubBucket:
        def __init__(self, data: bytes | None) -> None:
            self._data = data

        def blob(self, _name: str) -> _StubBlob:
            return _StubBlob(self._data)

    class _StubClient:
        def __init__(self, data: bytes | None) -> None:
            self._data = data

        def bucket(self, _name: str) -> _StubBucket:
            return _StubBucket(self._data)

    monkeypatch.setattr("google.cloud.storage.Client", lambda: _StubClient(blob_bytes))
    loaded = pdf_mod._load_summary("gs://bucket/path/summary.json")
    assert loaded["Medical Summary"] == "Remote summary"
