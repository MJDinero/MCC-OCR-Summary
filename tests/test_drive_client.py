import json
import logging
from typing import Any

import pytest

from src.services import drive_client as dc
from src.errors import DriveServiceError

# pylint: disable=protected-access,unused-argument


class _DummyDownloader:
    def __init__(self, buf):
        self._done = False
        self.buf = buf
    def next_chunk(self):
        if self._done:
            return None, True
        self._done = True
        return None, True


class _Req:
    def __init__(self, data: bytes):
        self.data = data
    # MediaIoBaseDownload expects .execute-like interface via its wrapper; we simulate stream by write in downloader


class _FilesResource:
    def __init__(self, pdf_bytes: bytes):
        self._pdf_bytes = pdf_bytes
        self.created: dict[str, Any] = {}
    def get_media(self, fileId: str):  # noqa: N802
        return _Req(self._pdf_bytes)
    def create(self, body, media_body, fields, enforceSingleParent=False):  # noqa: D401
        class _Exec:
            def __init__(self, outer, body):
                self.outer = outer
                self.body = body
                self.uri = "https://example.com/upload"
            def execute(self):
                self.outer.created = {"id": "new123", **self.body}
                return {"id": "new123"}
        return _Exec(self, body)


class _Service:
    def __init__(self, pdf_bytes: bytes, about_email: str = "user@example.com"):
        self._files = _FilesResource(pdf_bytes)
        self._about_email = about_email

    def files(self):  # noqa: D401
        return self._files

    def about(self):
        email = self._about_email

        class _AboutExec:
            def execute(self):
                return {"user": {"emailAddress": email}}

        class _About:
            def get(self, fields):
                assert fields == "user"
                return _AboutExec()

        return _About()


class _FakeMediaDownload:
    def __init__(self, buf, request):
        # write data immediately
        buf.write(request.data)
        self.done = False
    def next_chunk(self):
        if self.done:
            return None, True
        self.done = True
        return None, True


class _FakeMediaUpload:
    def __init__(self, *_a, **_k):
        pass


@pytest.fixture(autouse=True)
def patch_google(monkeypatch):
    # patch builder
    monkeypatch.setenv('DRIVE_REPORT_FOLDER_ID', 'out-folder')
    monkeypatch.setenv('DRIVE_SHARED_DRIVE_ID', '0AFPP3mbSAh_oUk9PVA')
    monkeypatch.setenv('DRIVE_INPUT_FOLDER_ID', 'in-folder')
    monkeypatch.setenv('PROJECT_ID', 'proj')
    monkeypatch.setenv('REGION', 'us')
    monkeypatch.setenv('DOC_AI_PROCESSOR_ID', 'pid')
    monkeypatch.setenv('OPENAI_API_KEY', 'k')
    impersonated_email = 'user@example.com'
    monkeypatch.setenv('DRIVE_IMPERSONATION_USER', impersonated_email)
    fake_sa_info = {
        "type": "service_account",
        "client_email": "svc@example.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\\nFAKE\\n-----END PRIVATE KEY-----\\n",
    }
    monkeypatch.setenv('GOOGLE_APPLICATION_CREDENTIALS', json.dumps(fake_sa_info))

    pdf_bytes = b"%PDF-1.4 minimal"  # minimal header

    def fake_from_file(path, scopes=None, subject=None):
        raise AssertionError("from_service_account_file should not be called in JSON mode")

    def fake_from_info(info, scopes=None, subject=None):
        assert info == fake_sa_info
        assert scopes == dc._SCOPES
        assert subject == impersonated_email
        return object()

    def fake_build(serviceName, version, credentials=None, cache_discovery=False):  # noqa: N802
        assert serviceName == "drive"
        assert version == "v3"
        assert credentials is not None
        return _Service(pdf_bytes, impersonated_email)

    monkeypatch.setattr(dc.service_account.Credentials, 'from_service_account_file', fake_from_file)
    monkeypatch.setattr(dc.service_account.Credentials, 'from_service_account_info', fake_from_info)
    monkeypatch.setattr(dc, 'build', fake_build)
    monkeypatch.setattr(dc, 'MediaIoBaseDownload', _FakeMediaDownload)
    monkeypatch.setattr(dc, 'MediaIoBaseUpload', _FakeMediaUpload)
    monkeypatch.setattr(dc, '_resolve_folder_metadata', lambda fid, log_context=None: {"id": fid, "driveId": "0AFPP3mbSAh_oUk9PVA"})
    yield


def test_download_pdf_success():
    data = dc.download_pdf('file123')
    assert data.startswith(b'%PDF-')


def test_download_pdf_not_pdf(monkeypatch):
    # Patch service to return non-PDF
    pdf_bytes = b"HELLO"  # wrong magic
    def fake_build(serviceName, version, credentials=None, cache_discovery=False):  # noqa: N802
        return _Service(pdf_bytes)
    monkeypatch.setattr(dc, 'build', fake_build)
    with pytest.raises(DriveServiceError):
        dc.download_pdf('file123')


def test_upload_pdf_success():
    pdf = b"%PDF-1.4 test"  # minimal
    # commit: clear cached config to pick up env set by fixture
    from src.config import get_config
    try:
        get_config.cache_clear()  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover
        pass
    fid = dc.upload_pdf(pdf, 'report.pdf')
    assert fid == 'new123'


def test_upload_pdf_reject_non_pdf():
    with pytest.raises(DriveServiceError):
        dc.upload_pdf(b'notpdf', 'report.pdf')


def test_download_pdf_logs_failure(monkeypatch, caplog):
    pdf_bytes = b"NOTPDF"

    def fake_build(serviceName, version, credentials=None, cache_discovery=False):  # noqa: N802
        return _Service(pdf_bytes)

    monkeypatch.setattr(dc, 'build', fake_build)
    caplog.set_level(logging.ERROR, logger='drive_client')
    with pytest.raises(DriveServiceError):
        dc.download_pdf('file123', log_context={'trace_id': 'trace-1'})
    events = [record for record in caplog.records if getattr(record, 'event', '') == 'drive_download_failure']
    assert events
    assert getattr(events[0], 'trace_id', None) == 'trace-1'
