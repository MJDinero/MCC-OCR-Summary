import pytest
from types import SimpleNamespace

from src.services import docai_helper as doc_helper_mod
from src.services.docai_helper import OCRService
from src.config import AppConfig


class DummyClient:
    def __init__(self):
        self.calls = 0
    def process_document(self, request):  # noqa: D401
        # Should not be called for large PDFs after escalation
        self.calls += 1
        return SimpleNamespace(document={"text": "sync", "pages": [{"text": "sync"}]})


def make_cfg():
    return AppConfig(project_id="proj", DOC_AI_LOCATION="us", DOC_AI_OCR_PROCESSOR_ID="pid")


def build_multi_page_pdf(pages: int) -> bytes:
    # Construct a synthetic PDF with minimal structure; ensure %PDF header
    # We'll append '/Type /Page' markers fewer times than real pages to simulate underestimation
    header = b"%PDF-1.4\n"
    body = b"".join(b"1 0 obj<</Type /Page>>endobj\n" for _ in range(max(1, pages // 10)))
    trailer = b"trailer<<>>\n%%EOF"
    return header + body + trailer


def test_escalation_triggers_batch(monkeypatch):
    # Large actual pages but heuristic occurrences intentionally low
    pdf_bytes = build_multi_page_pdf(120)

    # Patch PyPDF2.PdfReader to return object with pages list length 120
    class FakePdfReader:
        def __init__(self, _src):
            self.pages = [object()] * 120

    monkeypatch.setitem(__import__('sys').modules, 'PyPDF2', type('M', (), {'PdfReader': FakePdfReader, 'errors': type('E', (), {})}))

    # Patch batch_process_documents_gcs to capture invocation and return fake batch result
    called = {}
    from src import services as _services_pkg  # ensure package imported
    import src.services.docai_batch_helper as batch_mod

    def fake_batch(src, out, processor_id, region, project_id=None, clients=None):  # noqa: D401
        called['args'] = (src, out, processor_id, region, project_id)
        return {"text": "batched", "pages": [{"text": "p1"}], "batch_metadata": {"status": "succeeded"}}

    monkeypatch.setattr(batch_mod, 'batch_process_documents_gcs', fake_batch)
    # Ensure the symbol used inside docai_helper points to our fake after import
    if hasattr(doc_helper_mod, 'batch_process_documents_gcs'):
        monkeypatch.setattr(doc_helper_mod, 'batch_process_documents_gcs', fake_batch)

    client = DummyClient()
    svc = OCRService("pid", config=make_cfg(), client_factory=lambda _ep: client)
    out = svc.process(pdf_bytes)

    assert out["text"] == "batched"
    # Synchronous client should not be used
    assert client.calls == 0
    # Batch helper invoked
    assert 'args' in called
    meta = out["batch_metadata"]
    assert meta["status"] == "succeeded"