import os
from fastapi.testclient import TestClient
from src.main import create_app


def _env():
    os.environ['PROJECT_ID'] = 'proj'
    os.environ['REGION'] = 'us'
    os.environ['DOC_AI_PROCESSOR_ID'] = 'pid'
    os.environ['OPENAI_API_KEY'] = 'k'
    os.environ['DRIVE_INPUT_FOLDER_ID'] = 'in'
    os.environ['DRIVE_REPORT_FOLDER_ID'] = 'out'


def test_process_rejects_non_pdf_extension():
    _env()
    app = create_app()
    client = TestClient(app)
    files = {'file': ('doc.txt', b'hello', 'text/plain')}
    r = client.post('/process', files=files)
    assert r.status_code == 400
    assert 'File must have .pdf' in r.text
