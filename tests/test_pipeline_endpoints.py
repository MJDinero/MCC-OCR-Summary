import os
from fastapi.testclient import TestClient

from src.main import create_app


PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


def _set_env():
    os.environ['PROJECT_ID'] = 'proj'
    os.environ['REGION'] = 'us'
    os.environ['DOC_AI_PROCESSOR_ID'] = 'pid'
    os.environ['OPENAI_API_KEY'] = 'k'
    os.environ['DRIVE_INPUT_FOLDER_ID'] = 'in'
    os.environ['DRIVE_REPORT_FOLDER_ID'] = 'out'


class StubOCR:
    def __init__(self):
        self.calls = 0
    def process(self, data):
        self.calls += 1
        return {"text": "Patient John Doe age 40 Billing code ABC Legal none."}
    def close(self):
        pass

class StubSummariser:
    def summarise(self, text):
        return {
            'Patient Information':'John Doe, 40',
            'Medical Summary':'General checkup',
            'Billing Highlights':'Code ABC',
            'Legal / Notes':'None'
        }

class StubPDFWriter:
    def build(self, summary_dict):
        # pretend to build; ensure dict keys present
        assert 'Patient Information' in summary_dict
        return PDF_BYTES


def test_process_upload_flow():
    _set_env()
    app = create_app()
    app.state.ocr_service = StubOCR()
    app.state.summariser = StubSummariser()
    app.state.pdf_writer = StubPDFWriter()
    client = TestClient(app)
    files = {'file': ('doc.pdf', PDF_BYTES, 'application/pdf')}
    resp = client.post('/process', files=files)
    assert resp.status_code == 200
    assert resp.content.startswith(b'%PDF-')


def test_process_drive_flow(monkeypatch):
    _set_env()
    app = create_app()
    app.state.ocr_service = StubOCR()
    app.state.summariser = StubSummariser()
    app.state.pdf_writer = StubPDFWriter()

    def fake_download(fid):
        assert fid == 'file123'
        return PDF_BYTES
    up_ids = {}
    def fake_upload(data, name):
        assert data.startswith(b'%PDF-')
        up_ids['id'] = 'uploaded123'
        return 'uploaded123'
    monkeypatch.setenv('DRIVE_REPORT_FOLDER_ID','out')
    monkeypatch.setenv('DRIVE_INPUT_FOLDER_ID','in')
    monkeypatch.setenv('DOC_AI_PROCESSOR_ID','pid')
    monkeypatch.setenv('PROJECT_ID','proj')
    monkeypatch.setenv('REGION','us')
    monkeypatch.setenv('OPENAI_API_KEY','k')
    # Patch functions in module namespace used by app
    import src.main as m
    m.download_pdf = fake_download
    m.upload_pdf = fake_upload
    client = TestClient(app)
    r = client.get('/process_drive', params={'file_id':'file123'})
    assert r.status_code == 200
    assert r.json()['report_file_id'] == 'uploaded123'
