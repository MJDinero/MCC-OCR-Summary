import io
from types import SimpleNamespace

import pytest

from src.services import drive_client


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

    def create(self, *, body: dict, media_body: object, fields: str, supportsAllDrives: bool = False):
        if supportsAllDrives and self._fail_shared_drive:
            raise TypeError("supportsAllDrives not supported")
        self.created_payloads.append(body)
        return SimpleNamespace(execute=lambda: {'id': 'generated-id'})


@pytest.mark.parametrize('fail_shared_drive', [False, True])
def test_download_pdf_stubs(monkeypatch, fail_shared_drive):
    pdf_bytes = b"%PDF-1.4\n..."
    service = _DriveDownloadService(pdf_bytes, fail_shared_drive=fail_shared_drive)
    monkeypatch.setattr(drive_client, '_drive_service', lambda: service)
    monkeypatch.setattr(drive_client, 'MediaIoBaseDownload', _DownloadStub)
    result = drive_client.download_pdf('file-123')
    assert result == pdf_bytes


@pytest.mark.parametrize('fail_shared_drive', [False, True])
def test_upload_pdf_stubs(monkeypatch, fail_shared_drive):
    service = _DriveUploadService(fail_shared_drive=fail_shared_drive)
    monkeypatch.setattr(drive_client, '_drive_service', lambda: service)

    class _UploadStub:
        def __init__(self, buffer: io.BytesIO, mimetype: str, resumable: bool):
            self.buffer = buffer
            self.mimetype = mimetype
            self.resumable = resumable

    monkeypatch.setattr(drive_client, 'MediaIoBaseUpload', _UploadStub)

    class _Cfg:
        drive_report_folder_id = 'folder-id'
        summary_schema_version = '2025-10-01'
        project_id = 'test-project'

    monkeypatch.setattr(drive_client, 'get_config', lambda: _Cfg())

    file_id = drive_client.upload_pdf(b"%PDF-1.4\n...", 'report.pdf')
    assert file_id == 'generated-id'
    assert service.created_payloads
    payload = service.created_payloads[-1]
    assert payload['parents'] == ['folder-id']


def test_download_pdf_validations(monkeypatch):
    with pytest.raises(ValueError):
        drive_client.download_pdf('')

    class _BadService:
        def files(self):
            return self

        def get_media(self, fileId: str, supportsAllDrives: bool = False):
            return SimpleNamespace(payload=b'invalid')

    monkeypatch.setattr(drive_client, '_drive_service', lambda: _BadService())
    monkeypatch.setattr(drive_client, 'MediaIoBaseDownload', _DownloadStub)
    with pytest.raises(ValueError):
        drive_client.download_pdf('file-abc')


def test_upload_pdf_validations(monkeypatch):
    with pytest.raises(ValueError):
        drive_client.upload_pdf(b'not-pdf', 'name.pdf')

    class _Cfg:
        drive_report_folder_id = ''
        summary_schema_version = '2025-10-01'
        project_id = 'test-project'

    monkeypatch.setattr(drive_client, 'get_config', lambda: _Cfg())
    with pytest.raises(RuntimeError):
        drive_client.upload_pdf(b"%PDF-1.4\n...", 'name.pdf')
