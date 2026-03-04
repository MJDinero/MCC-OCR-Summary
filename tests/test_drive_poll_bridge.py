from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi.testclient import TestClient

from src.main import create_app


@dataclass
class _BlobStore:
    existing: set[str] = field(default_factory=set)
    uploads: dict[str, dict[str, Any]] = field(default_factory=dict)
    buckets: list[str] = field(default_factory=list)


class _FakeBlob:
    def __init__(self, name: str, store: _BlobStore):
        self.name = name
        self._store = store
        self.metadata: dict[str, str] | None = None

    def exists(self, client=None):  # noqa: D401
        return self.name in self._store.existing

    def upload_from_string(
        self,
        payload: bytes,
        *,
        content_type: str | None = None,
        if_generation_match: int | None = None,
    ) -> None:
        self._store.existing.add(self.name)
        self._store.uploads[self.name] = {
            "payload": payload,
            "content_type": content_type,
            "if_generation_match": if_generation_match,
            "metadata": dict(self.metadata or {}),
        }


class _FakeBucket:
    def __init__(self, store: _BlobStore):
        self._store = store

    def blob(self, name: str) -> _FakeBlob:
        return _FakeBlob(name, self._store)


class _FakeStorageClient:
    def __init__(self, store: _BlobStore):
        self._store = store

    def bucket(self, name: str) -> _FakeBucket:
        self._store.buckets.append(name)
        return _FakeBucket(self._store)


class _StubDriveClient:
    def __init__(self, candidates: list[dict[str, str | None]]) -> None:
        self._candidates = candidates
        self.list_calls: list[dict[str, str | int | None]] = []
        self.download_calls: list[dict[str, str | None]] = []

    def list_input_pdfs(
        self,
        folder_id: str,
        *,
        drive_id: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, str | None]]:
        self.list_calls.append(
            {"folder_id": folder_id, "drive_id": drive_id, "limit": limit}
        )
        return self._candidates

    def download_pdf(
        self,
        file_id: str,
        *,
        mime_type: str = "application/pdf",
        log_context: dict[str, Any] | None = None,
        quota_project: str | None = None,
        resource_key: str | None = None,
    ) -> bytes:
        self.download_calls.append(
            {
                "file_id": file_id,
                "mime_type": mime_type,
                "resource_key": resource_key,
            }
        )
        return b"%PDF-1.7 mirrored"


def _build_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("PROJECT_ID", "proj")
    monkeypatch.setenv("REGION", "us")
    monkeypatch.setenv("DOC_AI_PROCESSOR_ID", "pid")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("DRIVE_INPUT_FOLDER_ID", "drive-input-folder")
    monkeypatch.setenv("DRIVE_REPORT_FOLDER_ID", "drive-output-folder")
    monkeypatch.setenv("DRIVE_SHARED_DRIVE_ID", "0AFPP3mbSAh_oUk9PVA")
    monkeypatch.setenv("INTAKE_GCS_BUCKET", "mcc-intake")
    monkeypatch.setenv("OUTPUT_GCS_BUCKET", "mcc-output")
    monkeypatch.setenv("SUMMARY_BUCKET", "mcc-output")
    monkeypatch.setenv("PIPELINE_STATE_BACKEND", "memory")
    monkeypatch.setenv("INTERNAL_EVENT_TOKEN", "token")
    app = create_app()
    return TestClient(app)


def test_drive_poll_mirrors_new_files_and_skips_existing(monkeypatch):
    client = _build_client(monkeypatch)
    store = _BlobStore(existing={"uploads/drive/already-there.pdf"})
    storage_client = _FakeStorageClient(store)
    monkeypatch.setattr(
        "src.services.drive_bridge.storage.Client", lambda: storage_client
    )

    stub_drive = _StubDriveClient(
        [
            {"id": "new-drive-file", "name": "new.pdf", "resource_key": "rk-new"},
            {"id": "already-there", "name": "old.pdf", "resource_key": None},
        ]
    )
    client.app.state.drive_client = stub_drive
    monkeypatch.setenv("DRIVE_POLL_MAX_FILES", "2")

    resp = client.post("/process/drive/poll")
    assert resp.status_code == 200
    body = resp.json()
    assert body["listed_count"] == 2
    assert body["mirrored_count"] == 1
    assert body["duplicate_count"] == 1
    assert body["failed_count"] == 0
    assert body["mirrored"] == [
        {
            "drive_file_id": "new-drive-file",
            "object_uri": "gs://mcc-intake/uploads/drive/new-drive-file.pdf",
        }
    ]
    assert body["duplicates"] == [
        {
            "drive_file_id": "already-there",
            "object_uri": "gs://mcc-intake/uploads/drive/already-there.pdf",
        }
    ]

    assert stub_drive.list_calls == [
        {
            "folder_id": "drive-input-folder",
            "drive_id": "0AFPP3mbSAh_oUk9PVA",
            "limit": 2,
        }
    ]
    assert stub_drive.download_calls == [
        {
            "file_id": "new-drive-file",
            "mime_type": "application/pdf",
            "resource_key": "rk-new",
        }
    ]
    assert store.buckets == ["mcc-intake", "mcc-intake"]
    assert "uploads/drive/new-drive-file.pdf" in store.uploads
    upload = store.uploads["uploads/drive/new-drive-file.pdf"]
    assert upload["content_type"] == "application/pdf"
    assert upload["if_generation_match"] == 0
    assert upload["metadata"]["source"] == "drive-poll"
    assert upload["metadata"]["drive_file_id"] == "new-drive-file"
    assert upload["metadata"]["drive_input_folder_id"] == "drive-input-folder"
    assert upload["metadata"]["drive_file_name"] == "new.pdf"
    assert upload["metadata"]["drive_shared_drive_id"] == "0AFPP3mbSAh_oUk9PVA"


def test_drive_poll_returns_502_when_drive_listing_fails(monkeypatch):
    client = _build_client(monkeypatch)

    class _FailingDriveClient:
        def list_input_pdfs(self, *_args, **_kwargs):
            raise RuntimeError("drive api unavailable")

    client.app.state.drive_client = _FailingDriveClient()
    resp = client.post("/process/drive/poll")
    assert resp.status_code == 502
    assert resp.json()["detail"] == "Failed to list Drive input files"
