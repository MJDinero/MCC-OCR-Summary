"""Local Document AI stubs for offline tests."""

from __future__ import annotations

from types import SimpleNamespace


def _build_stub_document(text: str) -> SimpleNamespace:
    """Return a minimal object matching the Document AI shape the code expects."""

    text = text or "Stub Document AI payload"
    text_segments = [SimpleNamespace(start_index=0, end_index=len(text))]
    layout = SimpleNamespace(text_anchor=SimpleNamespace(text_segments=text_segments))
    page = SimpleNamespace(paragraphs=[], layout=layout)
    document = SimpleNamespace(text=text, pages=[page])
    return document


class StubDocumentProcessorServiceClient:
    """Sync stub used by `src.services.docai_helper`."""

    def __init__(self, *, text: str = "Stub Document AI text", **_: object) -> None:
        self._text = text

    def process_document(self, _request: object) -> SimpleNamespace:
        return SimpleNamespace(document=_build_stub_document(self._text))


class StubDocumentProcessorServiceAsyncClient:
    """Async stub consumed by `src.services.ocr_service`."""

    def __init__(self, *, text: str = "Stub Document AI text", **_: object) -> None:
        self._text = text

    async def process_document(self, _request: object) -> SimpleNamespace:
        return SimpleNamespace(document=_build_stub_document(self._text))


def install_docai_stub(monkeypatch) -> None:
    """Monkeypatch Document AI clients with the stub implementations."""

    try:
        import src.services.docai_helper as docai_helper
    except Exception:  # pragma: no cover - module import failure
        docai_helper = None
    try:
        import src.services.ocr_service as ocr_service
    except Exception:
        ocr_service = None

    if docai_helper and hasattr(docai_helper, "documentai"):
        monkeypatch.setattr(
            docai_helper.documentai,
            "DocumentProcessorServiceClient",
            StubDocumentProcessorServiceClient,
        )
    if ocr_service and hasattr(ocr_service, "documentai"):
        monkeypatch.setattr(
            ocr_service.documentai,
            "DocumentProcessorServiceAsyncClient",
            StubDocumentProcessorServiceAsyncClient,
        )
