import io
from types import SimpleNamespace

import pytest

from src.services import drive_client
from src.services.metrics import NullMetrics, PrometheusMetrics
from src.services import metrics as metrics_module


class _DownloadStub:
    def __init__(self, buffer: io.BytesIO, request: SimpleNamespace):
        self._buffer = buffer
        self._request = request
        self._done = False

    def next_chunk(self):
        if not self._done:
            self._buffer.write(self._request.payload)
            self._done = True
        return None, True


class _DriveDownloadService:
    def __init__(self, payload: bytes, fail_shared_drive: bool):
        self._payload = payload
        self._fail_shared_drive = fail_shared_drive

    def files(self):  # pragma: no cover - structural
        return self

    def get_media(self, fileId: str, supportsAllDrives: bool = False):
        if supportsAllDrives and self._fail_shared_drive:
            raise TypeError("supportsAllDrives not supported")
        return SimpleNamespace(payload=self._payload)


class _DriveUploadService:
    def __init__(self, fail_shared_drive: bool):
        self._fail_shared_drive = fail_shared_drive
        self.created_payloads: list[dict[str, object]] = []

    def files(self):  # pragma: no cover - structural
        return self

    def create(
        self,
        *,
        body: dict,
        media_body: object,
        fields: str,
        supportsAllDrives: bool = False,
        enforceSingleParent: bool = False,
    ):
        if supportsAllDrives and self._fail_shared_drive:
            raise TypeError("supportsAllDrives not supported")
        self.created_payloads.append(body)
        return SimpleNamespace(
            execute=lambda: {"id": "generated-id"}, uri="https://example.com/upload"
        )


@pytest.mark.parametrize("fail_shared_drive", [False, True])
def test_download_pdf_stubs(monkeypatch, fail_shared_drive):
    pdf_bytes = b"%PDF-1.4\n..."
    service = _DriveDownloadService(pdf_bytes, fail_shared_drive=fail_shared_drive)
    monkeypatch.setattr(drive_client, "_drive_service", lambda: service)
    monkeypatch.setattr(drive_client, "MediaIoBaseDownload", _DownloadStub)
    result = drive_client.download_pdf("file-123")
    assert result == pdf_bytes


@pytest.mark.parametrize("fail_shared_drive", [False, True])
def test_upload_pdf_stubs(monkeypatch, fail_shared_drive):
    service = _DriveUploadService(fail_shared_drive=fail_shared_drive)
    monkeypatch.setattr(drive_client, "_drive_service", lambda: service)
    monkeypatch.setattr(
        drive_client,
        "_resolve_folder_metadata",
        lambda fid: {"id": fid, "driveId": "shared-drive-id"},
    )

    class _UploadStub:
        def __init__(self, buffer: io.BytesIO, mimetype: str, resumable: bool):
            self.buffer = buffer
            self.mimetype = mimetype
            self.resumable = resumable

    monkeypatch.setattr(drive_client, "MediaIoBaseUpload", _UploadStub)

    class _Cfg:
        drive_report_folder_id = "folder-id"
        drive_shared_drive_id = "shared-drive-id"
        summary_schema_version = "2025-11-16"
        project_id = "test-project"

    monkeypatch.setattr(drive_client, "get_config", lambda: _Cfg())

    file_id = drive_client.upload_pdf(b"%PDF-1.4\n...", "report.pdf")
    assert file_id == "generated-id"
    assert service.created_payloads
    payload = service.created_payloads[-1]
    assert payload["parents"] == ["folder-id"]
    assert "driveId" not in payload


def test_upload_pdf_normalises_folder_id(monkeypatch):
    service = _DriveUploadService(fail_shared_drive=False)
    monkeypatch.setattr(drive_client, "_drive_service", lambda: service)
    monkeypatch.setattr(
        drive_client,
        "_resolve_folder_metadata",
        lambda fid: {"id": fid, "driveId": "shared-drive-id"},
    )

    class _Cfg:
        drive_report_folder_id = " https://drive.google.com/drive/folders/drive-input-folder-id?usp=sharing "
        drive_shared_drive_id = None
        summary_schema_version = "2025-11-16"
        project_id = "test-project"

    class _UploadStub:
        def __init__(self, buffer: io.BytesIO, mimetype: str, resumable: bool):
            self.buffer = buffer
            self.mimetype = mimetype
            self.resumable = resumable

    monkeypatch.setattr(drive_client, "MediaIoBaseUpload", _UploadStub)
    monkeypatch.setattr(drive_client, "get_config", lambda: _Cfg())

    file_id = drive_client.upload_pdf(b"%PDF-1.4\n...", "report.pdf")
    assert file_id == "generated-id"
    payload = service.created_payloads[-1]
    assert payload["parents"] == ["drive-input-folder-id"]
    assert "driveId" not in payload


def test_upload_pdf_supports_json_secret(monkeypatch):
    service = _DriveUploadService(fail_shared_drive=False)
    monkeypatch.setattr(drive_client, "_drive_service", lambda: service)
    monkeypatch.setattr(
        drive_client,
        "_resolve_folder_metadata",
        lambda fid: {"id": fid, "driveId": "shared-drive-id"},
    )

    class _Cfg:
        drive_report_folder_id = '{"folderId":"drive-input-folder-id","driveId":"shared-drive-id"}'
        drive_shared_drive_id = None
        summary_schema_version = "2025-11-16"
        project_id = "test-project"

    class _UploadStub:
        def __init__(self, buffer: io.BytesIO, mimetype: str, resumable: bool):
            self.buffer = buffer
            self.mimetype = mimetype
            self.resumable = resumable

    monkeypatch.setattr(drive_client, "MediaIoBaseUpload", _UploadStub)
    monkeypatch.setattr(drive_client, "get_config", lambda: _Cfg())

    file_id = drive_client.upload_pdf(b"%PDF-1.4\n...", "report.pdf")
    assert file_id == "generated-id"
    payload = service.created_payloads[-1]
    assert payload["parents"] == ["drive-input-folder-id"]
    assert "driveId" not in payload


def test_download_pdf_validations(monkeypatch):
    with pytest.raises(ValueError):
        drive_client.download_pdf("")

    class _BadService:
        def files(self):
            return self

        def get_media(self, fileId: str, supportsAllDrives: bool = False):
            return SimpleNamespace(payload=b"invalid")

    monkeypatch.setattr(drive_client, "_drive_service", lambda: _BadService())
    monkeypatch.setattr(drive_client, "MediaIoBaseDownload", _DownloadStub)
    with pytest.raises(ValueError):
        drive_client.download_pdf("file-abc")


def test_upload_pdf_validations(monkeypatch):
    with pytest.raises(ValueError):
        drive_client.upload_pdf(b"not-pdf", "name.pdf")

    class _Cfg:
        drive_report_folder_id = ""
        drive_shared_drive_id = None
        summary_schema_version = "2025-11-16"
        project_id = "test-project"

    monkeypatch.setattr(drive_client, "get_config", lambda: _Cfg())
    monkeypatch.setattr(
        drive_client,
        "_resolve_folder_metadata",
        lambda fid: {"id": fid, "driveId": "shared-drive-id"},
    )
    with pytest.raises(RuntimeError):
        drive_client.upload_pdf(b"%PDF-1.4\n...", "name.pdf")


def test_metrics_module_exercised(monkeypatch):
    metrics = PrometheusMetrics.default()
    metrics.observe_latency("drive_upload", 0.05, stage="drive")
    metrics.increment("drive_upload", stage="drive")
    with metrics.time("drive_upload", stage="drive"):
        pass

    class _App:
        def __init__(self):
            self.state = SimpleNamespace(_prometheus_instrumented=False)

        def get(self, _path):
            def decorator(handler):
                return handler

            return decorator

    fake_app = _App()
    monkeypatch.setattr(
        metrics_module,
        "CONTENT_TYPE_LATEST",
        "text/plain; version=0.0.4",
        raising=False,
    )
    monkeypatch.setattr(
        metrics_module, "generate_latest", lambda: b"metrics", raising=False
    )
    PrometheusMetrics.instrument_app(fake_app)
    PrometheusMetrics.instrument_app(fake_app)

    null_metrics = NullMetrics()
    null_metrics.increment("drive_upload", stage="drive")
    null_metrics.observe_latency("drive_upload", 0.05, stage="drive")

    assert metrics is PrometheusMetrics.default()
