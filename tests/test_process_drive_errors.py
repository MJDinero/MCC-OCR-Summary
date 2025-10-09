import os
from fastapi.testclient import TestClient
import pytest

from src.main import create_app

PDF_BYTES = b"%PDF-1.4 minimal"  # sufficient header for test


def _set_env():
    os.environ['PROJECT_ID'] = 'proj'
    os.environ['REGION'] = 'us'
    os.environ['DOC_AI_PROCESSOR_ID'] = 'pid'
    os.environ['OPENAI_API_KEY'] = 'k'
    os.environ['DRIVE_INPUT_FOLDER_ID'] = 'in'
    os.environ['DRIVE_REPORT_FOLDER_ID'] = 'out'


class StubOCR:
    def process(self, data):
        return { 'text': 'Sample OCR content' }


class StubSummariser:
    def summarise(self, text):
        return {
            'Patient Information': 'P',
            'Medical Summary': 'M',
            'Billing Highlights': 'B',
            'Legal / Notes': 'L'
        }


class StubPDFWriter:
    def build(self, summary_dict):
        return PDF_BYTES


def test_process_drive_upload_failure(monkeypatch):
    _set_env()
    app = create_app()
    app.state.ocr_service = StubOCR()
    app.state.summariser = StubSummariser()
    app.state.pdf_writer = StubPDFWriter()

    def fake_download(file_id: str):
        return PDF_BYTES
    def fake_upload(data: bytes, report_name: str):
        raise RuntimeError('upload failed')
    import src.main as m
    m.download_pdf = fake_download
    m.upload_pdf = fake_upload
    client = TestClient(app)
    r = client.get('/process_drive', params={'file_id': 'abc'})
    # Unhandled runtime error bubbles as 500
    assert r.status_code == 500
