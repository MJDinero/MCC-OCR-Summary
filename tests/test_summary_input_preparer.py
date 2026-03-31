from __future__ import annotations

from src.services import summary_input_preparer as preparer


def test_prepare_summary_input_prefers_native_text_for_readable_pdf(monkeypatch):
    class _ReadablePage:
        def __init__(self, text: str) -> None:
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class _ReadableReader:
        def __init__(self, _payload) -> None:
            rich_text = (
                "Follow-up visit for lumbar strain after a lifting injury. "
                "Lumbar tenderness improved with home stretching and ibuprofen. "
                "Continue conservative care and return in two weeks."
            )
            self.pages = [_ReadablePage(rich_text), _ReadablePage(rich_text)]

    monkeypatch.setattr(preparer, "PdfReader", _ReadableReader)

    prepared = preparer.prepare_summary_input_from_pdf_bytes(
        b"%PDF-1.4\nstub\n",
        job_metadata={"job_id": "job-123", "object_uri": "gs://bucket/input.pdf"},
    )

    assert prepared.requires_ocr is False
    assert prepared.text_source == "native_text"
    assert prepared.route_reason == "native_text_sufficient"
    assert prepared.text
    assert prepared.pages
    assert prepared.metadata_patch["summary_text_source"] == "native_text"
    assert prepared.metadata_patch["summary_requires_ocr"] is False


def test_prepare_summary_input_falls_back_to_ocr_for_blank_native_text(monkeypatch):
    class _BlankPage:
        def extract_text(self) -> str:
            return ""

    class _BlankReader:
        def __init__(self, _payload) -> None:
            self.pages = [_BlankPage(), _BlankPage()]

    monkeypatch.setattr(preparer, "PdfReader", _BlankReader)

    prepared = preparer.prepare_summary_input_from_pdf_bytes(b"%PDF-1.4\nstub\n")

    assert prepared.requires_ocr is True
    assert prepared.text_source == "ocr"
    assert prepared.route_reason == "scan_or_image_only"
    assert prepared.metadata_patch["summary_text_source"] == "ocr"
    assert prepared.metadata_patch["summary_requires_ocr"] is True


def test_prepare_summary_input_marks_low_confidence_native_text_for_ocr(monkeypatch):
    class _NoisyPage:
        def __init__(self, text: str) -> None:
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class _NoisyReader:
        def __init__(self, _payload) -> None:
            self.pages = [
                _NoisyPage("12 34 56 78 " * 12),
                _NoisyPage("90 12 34 56 " * 12),
                _NoisyPage("AA 11 BB 22 " * 12),
            ]

    monkeypatch.setattr(preparer, "PdfReader", _NoisyReader)

    prepared = preparer.prepare_summary_input_from_pdf_bytes(b"%PDF-1.4\nstub\n")

    assert prepared.requires_ocr is True
    assert prepared.route_reason == "native_text_low_confidence"
    assert prepared.triage_metrics.alpha_ratio < 0.55
