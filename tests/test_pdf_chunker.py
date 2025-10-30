import json
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

from PyPDF2 import PdfWriter

from src.services.chunker import PDFChunker


class FakeBlob:
    def __init__(self, bucket: "FakeBucket", name: str, data: bytes | None = None):
        self.bucket = bucket
        self.name = name
        self._data = data or b""
        self.metadata: dict[str, str] = {}
        self.cache_control: str | None = None
        self._content_type: str | None = None
        self.deleted = False

    def download_to_filename(self, filename: str) -> None:
        Path(filename).write_bytes(self._data)

    def upload_from_filename(
        self, filename: str, content_type: str | None = None
    ) -> None:
        self._data = Path(filename).read_bytes()
        self._content_type = content_type

    def upload_from_string(
        self, data: bytes | str, content_type: str | None = None
    ) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._data = data
        self._content_type = content_type

    def delete(self) -> None:
        self.deleted = True
        self.bucket._store.pop(self.name, None)

    @property
    def data(self) -> bytes:
        return self._data


class FakeBucket:
    def __init__(self, name: str):
        self.name = name
        self._store: dict[str, FakeBlob] = {}

    def blob(self, name: str) -> FakeBlob:
        if name not in self._store:
            self._store[name] = FakeBlob(self, name)
        return self._store[name]


class FakeStorageClient:
    def __init__(self):
        self._buckets: dict[str, FakeBucket] = {}

    def bucket(self, name: str) -> FakeBucket:
        if name not in self._buckets:
            self._buckets[name] = FakeBucket(name)
        return self._buckets[name]

    def list_blobs(self, bucket_name: str, prefix: str):
        bucket = self.bucket(bucket_name)
        return [blob for blob in bucket._store.values() if blob.name.startswith(prefix)]


def _build_pdf(page_count: int) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=72, height=72)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_pdf_chunker_splits_and_writes_manifest(tmp_path):
    storage = FakeStorageClient()
    source_bucket = storage.bucket("source-bucket")
    source_blob = source_bucket.blob("docs/source.pdf")
    source_blob.upload_from_string(_build_pdf(6))

    chunker = PDFChunker(
        storage_client=storage,
        max_pages=2,
        retention_days=7,
        artifact_bucket="artifact-bucket",
        artifact_prefix="tmp/chunks",
        tmp_dir=str(tmp_path),
    )

    artifacts = list(
        chunker.chunk_pdf(
            "gs://source-bucket/docs/source.pdf",
            job_id="job-123",
            metadata={"env": "test"},
        )
    )

    assert len(artifacts) == 3
    assert artifacts[0].page_start == 1 and artifacts[0].page_end == 2
    assert artifacts[-1].page_start == 5 and artifacts[-1].page_end == 6

    manifest = chunker.last_manifest
    assert manifest is not None
    assert manifest.job_id == "job-123"
    assert manifest.manifest_uri.endswith("manifest.json")
    assert len(manifest.artifacts) == len(artifacts)

    dest_bucket = storage.bucket("artifact-bucket")
    for artifact in artifacts:
        blob_name = artifact.uri.split("gs://artifact-bucket/")[1]
        blob = dest_bucket.blob(blob_name)
        assert blob.metadata["retain_until"] == artifact.expires_at
        assert blob.metadata["env"] == "test"

    manifest_path = manifest.manifest_uri.split("gs://artifact-bucket/")[1]
    manifest_blob = dest_bucket.blob(manifest_path)
    payload = json.loads(manifest_blob.data.decode("utf-8"))
    assert payload["job_id"] == "job-123"
    assert payload["max_pages_per_chunk"] == 2
    assert len(payload["artifacts"]) == 3


def test_pdf_chunker_cleanup_removes_expired_objects(tmp_path):
    storage = FakeStorageClient()
    source_bucket = storage.bucket("source")
    source_blob = source_bucket.blob("input/report.pdf")
    source_blob.upload_from_string(_build_pdf(4))

    chunker = PDFChunker(
        storage_client=storage,
        max_pages=2,
        retention_days=1,
        artifact_bucket="artifact",
        artifact_prefix="phi/chunks",
        tmp_dir=str(tmp_path),
    )

    artifacts = list(
        chunker.chunk_pdf(
            "gs://source/input/report.pdf",
            job_id="cleanup-job",
        )
    )
    assert artifacts

    expires = datetime.fromisoformat(artifacts[0].expires_at.replace("Z", "+00:00"))
    deleted = chunker.cleanup_expired_artifacts(now=expires + timedelta(seconds=1))
    # Both chunks and manifest should be removed
    assert deleted
    # Ensure storage listing no longer returns objects under prefix
    remaining = list(storage.list_blobs("artifact", prefix="phi/chunks/"))
    assert not remaining
