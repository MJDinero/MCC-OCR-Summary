import json
from types import SimpleNamespace

from src.utils.pdf_splitter import split_pdf_by_page_limit


class StubOperation:
    def __init__(self):
        self._done = True
        self.name = "operations/mock"

    def done(self):  # noqa: D401
        return self._done

    def result(self):  # noqa: D401
        return None


class StubDocAIClient:
    def __init__(self):
        self.requests = []

    def batch_process_documents(self, request):  # noqa: D401
        self.requests.append(request)
        return StubOperation()


class StubManifestBlob:
    def __init__(self, bucket_name: str, name: str, store: dict):
        self.bucket = SimpleNamespace(name=bucket_name)
        self.name = name
        self._store = store

    def upload_from_string(self, data, content_type=None, if_generation_match=None):  # noqa: D401
        self._store[self.name] = {
            "data": data,
            "content_type": content_type,
            "if_generation_match": if_generation_match,
        }


class StubBlob:
    def __init__(self, bucket_name: str, name: str):
        self.name = name
        self.bucket = SimpleNamespace(name=bucket_name)


class StubBucket:
    def __init__(self, name: str, store: dict):
        self.name = name
        self._store = store

    def blob(self, name: str):  # noqa: D401
        return StubManifestBlob(self.name, name, self._store)


class StubStorageClient:
    def __init__(self):
        self._manifests = {}
        self._list = [
            StubBlob("bucket", "split/job/part-0001.pdf"),
            StubBlob("bucket", "split/job/part-0002.pdf"),
            StubBlob("bucket", "split/job/metadata.json"),
        ]

    def bucket(self, name: str):  # noqa: D401
        return StubBucket(name, self._manifests)

    def list_blobs(self, bucket_name: str, prefix: str):  # noqa: D401
        return [blob for blob in self._list if blob.name.startswith(prefix)]


def test_splitter_builds_manifest_and_parts(monkeypatch):
    docai = StubDocAIClient()
    storage = StubStorageClient()
    result = split_pdf_by_page_limit(
        "gs://bucket/source.pdf",
        project_id="proj",
        location="us",
        splitter_processor_id="proc",
        output_prefix="gs://bucket/split/job/",
        storage_client=storage,
        docai_client=docai,
    )
    assert result.parts == [
        "gs://bucket/split/job/part-0001.pdf",
        "gs://bucket/split/job/part-0002.pdf",
    ]
    assert result.manifest_gcs_uri == "gs://bucket/split/job/manifest.json"
    manifest_blob = storage._manifests["split/job/manifest.json"]
    raw = manifest_blob["data"]
    payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    assert payload["input_uri"] == "gs://bucket/source.pdf"
    assert len(payload["parts"]) == 2
    assert manifest_blob["if_generation_match"] == 0
