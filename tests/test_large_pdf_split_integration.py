import json

import pytest
from PyPDF2 import PdfWriter

from src.services.docai_helper import OCRService

pytestmark = pytest.mark.integration


class DummyBatch:
    def __init__(self):
        self.calls = []

    def __call__(
        self, input_uri, output_uri, processor_id, region, project_id=None, clients=None
    ):
        self.calls.append(input_uri)
        pages = [
            {"layout": {"text": f"Page {i} part {len(self.calls)}"}}
            for i in range(1, 121)
        ]
        return {
            "text": " ".join(p["layout"]["text"] for p in pages),
            "pages": pages,
            "batch_metadata": {
                "status": "succeeded",
                "output_uri": f"gs://out/part{len(self.calls)}",
            },
        }


@pytest.fixture()
def large_pdf(tmp_path, monkeypatch):
    # Build a synthetic 263-page PDF (empty pages)
    writer = PdfWriter()
    for _ in range(263):
        writer.add_blank_page(width=72, height=72)
    pdf_path = tmp_path / "large.pdf"
    with open(pdf_path, "wb") as f:
        writer.write(f)
    return pdf_path


@pytest.fixture(autouse=True)
def patch_batch(monkeypatch):
    dummy = DummyBatch()
    monkeypatch.setattr("src.services.docai_helper.batch_process_documents_gcs", dummy)
    return dummy


@pytest.fixture(autouse=True)
def patch_storage(monkeypatch):
    class DummyBlob:
        def __init__(self, name):
            self.name = name

        def upload_from_filename(self, *_a, **_k):
            return None

        def upload_from_string(self, *_a, **_k):
            return None

        def download_as_bytes(self):
            return json.dumps({"document": {"text": "", "pages": []}}).encode("utf-8")

    class DummyBucket:
        def blob(self, name):
            return DummyBlob(name)

    class DummyStorage:
        def bucket(self, *_):
            return DummyBucket()

        def list_blobs(self, *_args, **_kwargs):
            return []

    monkeypatch.setattr(
        "src.services.docai_batch_helper.storage.Client", lambda: DummyStorage()
    )
    return True


def test_large_pdf_uses_async_batch_path(large_pdf, patch_batch, monkeypatch):
    # Patch config references used by OCRService
    from src.config import get_config

    cfg = get_config()
    monkeypatch.setattr(cfg, "project_id", "proj")
    monkeypatch.setattr(cfg, "region", "us")
    service = OCRService(
        processor_id="processor123", config=cfg, client_factory=lambda endpoint: None
    )  # client unused in batch path

    result = service.process(str(large_pdf))

    # Expect batch path with automatic batching (single invocation)
    meta = result.get("batch_metadata") or {}
    assert meta.get("batch_mode") == "async_auto"
    assert len(patch_batch.calls) == 1
    assert len(result.get("pages") or []) == 120
