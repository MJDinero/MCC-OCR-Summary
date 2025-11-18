from types import SimpleNamespace

from src.services import docai_helper as doc_helper_mod
from src.services.docai_helper import OCRService
from src.config import AppConfig


class DummyClient:
    def __init__(self):
        self.calls = 0

    def process_document(self, request):  # noqa: D401
        self.calls += 1
        return SimpleNamespace(document={"text": "sync", "pages": [{"text": "sync"}]})


def make_cfg():
    return AppConfig(
        project_id="proj",
        DOC_AI_LOCATION="us",
        DOC_AI_OCR_PROCESSOR_ID="pid",
        DOC_AI_SPLITTER_PROCESSOR_ID="splitter",
    )


def build_multi_page_pdf(markers: int) -> bytes:
    header = b"%PDF-1.4\n"
    body = b"".join(b"1 0 obj<</Type /Page>>endobj\n" for _ in range(markers))
    trailer = b"trailer<<>>\n%%EOF"
    return header + body + trailer


def test_escalation_triggers_batch(monkeypatch):
    pdf_bytes = build_multi_page_pdf(80)  # heuristic >30 triggers batch

    called = {}
    import src.services.docai_batch_helper as batch_mod

    def fake_batch(
        src, out, processor_id, region, project_id=None, clients=None
    ):  # noqa: D401
        called["args"] = (src, out, processor_id, region, project_id)
        return {
            "text": "batched",
            "pages": [{"text": "p1"}],
            "batch_metadata": {"status": "succeeded"},
        }

    monkeypatch.setattr(batch_mod, "batch_process_documents_gcs", fake_batch)
    if hasattr(doc_helper_mod, "batch_process_documents_gcs"):
        monkeypatch.setattr(doc_helper_mod, "batch_process_documents_gcs", fake_batch)

    client = DummyClient()
    svc = OCRService(
        "pid",
        config=make_cfg(),
        client_factory=lambda _ep: client,
        force_split_min_pages=200,
    )
    out = svc.process(pdf_bytes)

    assert out["text"] == "batched"
    assert client.calls == 0
    assert "args" in called
    assert out["batch_metadata"]["status"] == "succeeded"
