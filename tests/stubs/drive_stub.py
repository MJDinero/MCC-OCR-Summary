"""Google Drive stubs for offline testing."""

from __future__ import annotations

import io
from typing import Any, Dict


class _ExecuteWrapper:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload

    def execute(self) -> Dict[str, Any]:
        return self._payload


class _MediaRequest:
    def __init__(self, data: bytes) -> None:
        self.data = data


class StubDriveFilesResource:
    def __init__(self, service: "StubDriveService") -> None:
        self._service = service

    def get_media(self, fileId: str, **_: object) -> _MediaRequest:
        data = self._service.files_store.get(fileId, b"%PDF-1.4\n%%EOF")
        return _MediaRequest(data)

    def get(self, fileId: str, **_: object) -> _ExecuteWrapper:
        metadata = self._service.folder_meta.get(
            fileId,
            {"id": fileId, "driveId": "0AStubDrive", "permissionIds": [], "capabilities": {"canAddChildren": True}},
        )
        return _ExecuteWrapper(metadata)

    def create(self, body: Dict[str, Any], media_body: "StubMediaUpload", **_: object) -> _ExecuteWrapper:
        file_id = f"stub-{len(self._service.files_store) + 1}"
        self._service.files_store[file_id] = media_body.getvalue()
        payload = {
            "id": file_id,
            "driveId": "0AStubDrive",
            "name": body.get("name"),
            "parents": body.get("parents", []),
            "webViewLink": f"https://drive.example/{file_id}",
        }
        return _ExecuteWrapper(payload)


class StubDriveService:
    def __init__(self) -> None:
        self.files_store: Dict[str, bytes] = {"stub-file": b"%PDF-1.4\n%%EOF"}
        self.folder_meta: Dict[str, Dict[str, Any]] = {}

    def files(self) -> StubDriveFilesResource:
        return StubDriveFilesResource(self)


class StubMediaDownload:
    def __init__(self, buf: io.BytesIO, request: _MediaRequest) -> None:
        self._buf = buf
        self._request = request
        self._done = False

    def next_chunk(self):
        if self._done:
            return None, True
        self._buf.write(self._request.data)
        self._done = True
        return None, True


class StubMediaUpload:
    def __init__(self, stream: io.BytesIO, **_: object) -> None:
        position = stream.tell()
        stream.seek(0)
        self._data = stream.read()
        stream.seek(position)

    def getvalue(self) -> bytes:
        return self._data


class _StubCredentials:
    def with_subject(self, _subject: str | None) -> "_StubCredentials":
        return self


def install_drive_stub(monkeypatch) -> None:
    import src.services.drive_client as drive_client

    stub_service = StubDriveService()

    def _build_stub(serviceName: str, version: str, credentials=None, cache_discovery=False):  # noqa: N803
        return stub_service

    monkeypatch.setattr(drive_client, "build", _build_stub)
    monkeypatch.setattr(drive_client.service_account.Credentials, "from_service_account_info", lambda *args, **kwargs: _StubCredentials())
    monkeypatch.setattr(drive_client.service_account.Credentials, "from_service_account_file", lambda *args, **kwargs: _StubCredentials())
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "{}")
    monkeypatch.setattr(drive_client, "MediaIoBaseDownload", StubMediaDownload)
    monkeypatch.setattr(drive_client, "MediaIoBaseUpload", StubMediaUpload)
