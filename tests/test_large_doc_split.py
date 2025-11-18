import io

from pypdf import PdfWriter

from src.config import AppConfig
from src.services import docai_helper
from src.services.docai_helper import OCRService


def _make_pdf_pages(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _dummy_client(_endpoint: str):
    class _Client:
        def process_document(self, request):  # pragma: no cover - defensive guard
            raise AssertionError(f"Unexpected process_document call: {request}")

    return _Client()


def test_local_split_for_31_pages(monkeypatch):
    pdf_bytes = _make_pdf_pages(31)
    decisions: list[dict] = []

    def record_decision(
        *, pages: int, decision: str, cfg, request_id: str, retry_on_page_limit: bool = False
    ) -> None:
        decisions.append(
            {
                "pages": pages,
                "decision": decision,
                "retry": retry_on_page_limit,
                "request_id": request_id,
            }
        )

    calls = {"count": 0}

    def fake_process_chunk(**_kwargs):
        calls["count"] += 1
        return {
            "text": f"chunk-{calls['count']}",
            "pages": [{"page_number": 1, "text": f"page-{calls['count']}"}],
        }

    monkeypatch.setattr(docai_helper, "_log_decision", record_decision)
    monkeypatch.setattr(docai_helper, "_process_chunk_with_docai", fake_process_chunk)

    cfg = AppConfig(project_id="proj", DOC_AI_OCR_PROCESSOR_ID="pid")
    cfg.doc_ai_splitter_id = None
    cfg.doc_ai_splitter_id = None
    service = OCRService(
        processor_id="pid",
        config=cfg,
        doc_ai_splitter_id=None,
        force_split_min_pages=30,
        client_factory=_dummy_client,
    )

    result = service.process(pdf_bytes)

    assert result["text"]
    assert len(result["pages"]) >= 2
    assert calls["count"] >= 2  # multiple chunks processed locally
    assert decisions, "Expected docai_decision log entries"
    assert decisions[0]["decision"] == "local_pypdf_split"


def test_page_limit_fallback_to_local_split(monkeypatch):
    pdf_bytes = _make_pdf_pages(5)
    decisions: list[dict] = []

    def record_decision(
        *, pages: int, decision: str, cfg, request_id: str, retry_on_page_limit: bool = False
    ) -> None:
        decisions.append(
            {"pages": pages, "decision": decision, "retry": retry_on_page_limit}
        )

    calls = {"count": 0}

    def flaky_process_chunk(**_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise docai_helper._ChunkPageLimitExceeded("PAGE_LIMIT_EXCEEDED")
        return {
            "text": "chunk-ok",
            "pages": [{"page_number": 1, "text": "page-ok"}],
        }

    monkeypatch.setattr(docai_helper, "_log_decision", record_decision)
    monkeypatch.setattr(docai_helper, "_process_chunk_with_docai", flaky_process_chunk)

    cfg = AppConfig(project_id="proj", DOC_AI_OCR_PROCESSOR_ID="pid")
    service = OCRService(
        processor_id="pid",
        config=cfg,
        doc_ai_splitter_id=None,
        force_split_min_pages=50,  # ensure initial attempt is online
        client_factory=_dummy_client,
    )

    result = service.process(pdf_bytes)

    assert result["text"] == "chunk-ok"
    assert calls["count"] >= 2  # retry path invoked
    assert any(
        entry["decision"] == "local_pypdf_split_retry" and entry["retry"]
        for entry in decisions
    )
