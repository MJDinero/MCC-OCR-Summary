import os
import pytest

@pytest.fixture(autouse=True)
def _base_env(monkeypatch):
    # Ensure minimal required env for app/config across tests
    monkeypatch.setenv('PROJECT_ID', os.environ.get('PROJECT_ID', 'test-project'))
    monkeypatch.setenv('DOC_AI_LOCATION', os.environ.get('DOC_AI_LOCATION', 'us'))
    monkeypatch.setenv('DOC_AI_OCR_PROCESSOR_ID', os.environ.get('DOC_AI_OCR_PROCESSOR_ID', 'pid'))
    monkeypatch.setenv('OPENAI_API_KEY', os.environ.get('OPENAI_API_KEY', 'dummy'))
    monkeypatch.setenv('STUB_MODE', os.environ.get('STUB_MODE', 'true'))
    yield
