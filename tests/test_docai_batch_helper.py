import json
from types import SimpleNamespace

import pytest

from src.services.docai_batch_helper import batch_process_documents_gcs, _BatchClients
from src.errors import OCRServiceError, ValidationError

PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


class DummyBlob:
    def __init__(self, name: str, data: bytes):
        self.name = name
        self._data = data

    def download_as_bytes(self):  # noqa: D401
        return self._data

    # Upload API compatibility
    def upload_from_filename(self, filename: str):  # noqa: D401
        with open(filename, 'rb') as f:
            self._data = f.read()


class DummyBucket:
    def __init__(self, name: str, storage):
        self.name = name
        self._storage = storage

    def blob(self, path: str):  # noqa: D401
        # Return existing or create placeholder
        full = path
        blob = self._storage._blobs.get(full)
        if not blob:
            blob = DummyBlob(full, b"")
            self._storage._blobs[full] = blob
        return blob


class DummyStorageClient:
    def __init__(self):
        self._buckets = {}
        self._blobs = {}

    def bucket(self, name: str):  # noqa: D401
        bk = self._buckets.get(name)
        if not bk:
            bk = DummyBucket(name, self)
            self._buckets[name] = bk
        return bk

    def list_blobs(self, bucket_name: str, prefix: str):  # noqa: D401
        for name, blob in self._blobs.items():
            if name.startswith(prefix):
                yield blob


class DummyOperation:
    def __init__(self, metadata=None):
        self._done = False
        self._polls = 0
        self.metadata = metadata or SimpleNamespace(individual_process_statuses=[1, 2, 3])
        self.name = "operations/123"

    def done(self):  # noqa: D401
        # Simulate completion after 2 polls
        self._polls += 1
        if self._polls > 2:
            self._done = True
        return self._done

    def result(self):  # noqa: D401
        if not self._done:
            raise RuntimeError("not done")
        return True


class DummyDocAIClient:
    def __init__(self, storage_client: DummyStorageClient):
        self.storage_client = storage_client

    def batch_process_documents(self, request):  # noqa: D401
        # Populate fake output JSON immediately so once operation completes polling will read it
        output_prefix = request["document_output_config"]["gcs_output_config"]["gcs_uri"].replace("gs://quantify-agent-output/", "")
        # Simulate two shard JSON outputs
        for i in range(2):
            pages = [{"layout": {"text": f"page {i+1}"}}]
            doc = {"text": f"full text {i+1}", "pages": pages}
            blob_name = f"{output_prefix}output-{i}.json"
            self.storage_client._blobs[blob_name] = DummyBlob(blob_name, json.dumps({"document": doc}).encode("utf-8"))
        return DummyOperation()


@pytest.fixture
def tmp_pdf(tmp_path):
    p = tmp_path / "test.pdf"
    p.write_bytes(PDF_BYTES)
    return p


def test_batch_process_local_upload(tmp_pdf, monkeypatch):
    storage_client = DummyStorageClient()
    doc_client = DummyDocAIClient(storage_client)
    clients = _BatchClients(docai=doc_client, storage=storage_client)  # type: ignore[arg-type]
    result = batch_process_documents_gcs(str(tmp_pdf), None, processor_id="pid", region="us", project_id="proj", clients=clients)
    assert result["text"].startswith("full text")
    assert len(result["pages"]) == 2
    meta = result["batch_metadata"]
    assert meta["status"] == "succeeded"
    # pages_processed may come from operation metadata (simulated 3) which can differ
    assert meta["pages_processed"] >= 2
    assert meta["output_uri"].startswith("gs://quantify-agent-output/")


def test_batch_requires_pdf(monkeypatch, tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("hello")
    with pytest.raises(ValidationError):
        batch_process_documents_gcs(str(f), None, "pid", "us", project_id="proj", clients=_BatchClients(docai=DummyDocAIClient(DummyStorageClient()), storage=DummyStorageClient()))


def test_batch_failure_missing_output(monkeypatch, tmp_pdf):
    # Create a client that does NOT write json outputs
    class NoOutputDocAI(DummyDocAIClient):
        def batch_process_documents(self, request):  # noqa: D401
            return DummyOperation()
    storage_client = DummyStorageClient()
    clients = _BatchClients(docai=NoOutputDocAI(storage_client), storage=storage_client)
    with pytest.raises(OCRServiceError):
        batch_process_documents_gcs(str(tmp_pdf), None, "pid", "us", project_id="proj", clients=clients)
