import builtins  # commit: drive_client tests added
import types
import pytest
import sys

from src.services import drive_client as dc


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
        self.created = {}
    def get_media(self, fileId: str):  # noqa: N802
        return _Req(self._pdf_bytes)
    def create(self, body, media_body, fields):  # noqa: D401
        class _Exec:
            def __init__(self, outer, body):
                self.outer = outer; self.body = body
            def execute(self):
                self.outer.created = {"id": "new123", **self.body}
                return {"id": "new123"}
        return _Exec(self, body)


class _Service:
    def __init__(self, pdf_bytes: bytes):
        self._files = _FilesResource(pdf_bytes)
    def files(self):  # noqa: D401
        return self._files


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
    monkeypatch.setenv('DRIVE_INPUT_FOLDER_ID', 'in-folder')
    monkeypatch.setenv('PROJECT_ID', 'proj')
    monkeypatch.setenv('REGION', 'us')
    monkeypatch.setenv('DOC_AI_PROCESSOR_ID', 'pid')
    monkeypatch.setenv('OPENAI_API_KEY', 'k')
    pdf_bytes = b"%PDF-1.4 minimal"  # minimal header
    def fake_build(serviceName, version, credentials=None, cache_discovery=False):  # noqa: N802
        return _Service(pdf_bytes)
    monkeypatch.setattr(dc, 'build', fake_build)
    monkeypatch.setattr(dc, 'MediaIoBaseDownload', _FakeMediaDownload)
    monkeypatch.setattr(dc, 'MediaIoBaseUpload', _FakeMediaUpload)
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
    with pytest.raises(ValueError):
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
    with pytest.raises(ValueError):
        dc.upload_pdf(b'notpdf', 'report.pdf')
