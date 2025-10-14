import json
from types import SimpleNamespace

import pytest

from src.services.docai_helper import OCRService
from src.services.docai_batch_helper import _BatchClients
from src.config import AppConfig

# Build a large PDF bytes fixture: Minimalistic structure with many /Type /Page markers
# Not a valid multi-page PDF for rendering, but passes heuristic page counting.
PAGE_MARKER = b"/Type /Page"
BASE_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\n" + PAGE_MARKER + b"\ntrailer<<>>\n%%EOF"
LARGE_PDF_BYTES = b"%PDF-1.4\n" + (PAGE_MARKER + b"\n") * 120 + b"trailer<<>>\n%%EOF"

class DummyBlob:
    def __init__(self, name: str, data: bytes):
        self.name = name
        self._data = data

    def download_as_bytes(self):
        return self._data

    def upload_from_filename(self, filename: str):
        with open(filename, 'rb') as f:
            self._data = f.read()

class DummyStorageClient:
    def __init__(self):
        self._blobs = {}
    def bucket(self, name: str):  # returns self for blob()
        return self
    def blob(self, path: str):
        b = self._blobs.get(path)
        if not b:
            b = DummyBlob(path, b"")
            self._blobs[path] = b
        return b
    def list_blobs(self, bucket_name: str, prefix: str):
        for name, blob in self._blobs.items():
            if name.startswith(prefix):
                yield blob

class DummyOperation:
    def __init__(self):
        self._done = False
        self._polls = 0
        self.metadata = SimpleNamespace(individual_process_statuses=[1]*50)
        self.name = "operations/big"
    def done(self):
        self._polls += 1
        if self._polls > 1:
            self._done = True
        return self._done
    def result(self):
        if not self._done:
            raise RuntimeError("not done")
        return True

class DummyDocAIClient:
    def __init__(self, storage_client):
        self.storage_client = storage_client
    def batch_process_documents(self, request):
        output_prefix = request["document_output_config"]["gcs_output_config"]["gcs_uri"].replace("gs://quantify-agent-output/", "")
        # Write single large JSON
        pages = [{"layout": {"text": f"page {i+1}"}} for i in range(50)]
        doc = {"text": "full text big", "pages": pages}
        blob_name = f"{output_prefix}output-0.json"
        self.storage_client._blobs[blob_name] = DummyBlob(blob_name, json.dumps({"document": doc}).encode('utf-8'))
        return DummyOperation()

class BatchAwareOCR(OCRService):  # type: ignore[misc]
    def __init__(self):
        # Provide minimal config
        self.processor_id = "pid"
        self.config = AppConfig(project_id="proj", DOC_AI_LOCATION="us", DOC_AI_OCR_PROCESSOR_ID="pid")
        self._cfg = self.config
        self._client_factory = lambda endpoint: None  # unused in batch path
        self._endpoint = "us-documentai.googleapis.com"
        self._client = None

@pytest.fixture
def large_pdf(tmp_path):
    p = tmp_path / "large.pdf"
    p.write_bytes(LARGE_PDF_BYTES)
    return p


def test_large_pdf_triggers_batch(monkeypatch, large_pdf):
    storage_client = DummyStorageClient()
    doc_client = DummyDocAIClient(storage_client)
    monkeypatch.setattr("src.services.docai_batch_helper._default_clients", lambda region: _BatchClients(docai=doc_client, storage=storage_client))
    ocr = BatchAwareOCR()
    result = ocr.process(str(large_pdf))
    assert result["batch_metadata"]["status"] == "succeeded"
    assert result["batch_metadata"]["pages_processed"] == 50
    assert result["text"].startswith("full text big")
