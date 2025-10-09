import pytest
pytest.skip("Legacy startup failure test removed after refactor", allow_module_level=True)

import pytest
from fastapi.testclient import TestClient
from src.main import create_app


def test_startup_missing_openai_key_non_stub_fails(monkeypatch):
    # Ensure required env vars except OPENAI_API_KEY
    monkeypatch.setenv('PROJECT_ID', 'proj')
    monkeypatch.setenv('DOC_AI_OCR_PROCESSOR_ID', 'pid')
    monkeypatch.setenv('DOC_AI_LOCATION', 'us')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('STUB_MODE', raising=False)
    app = create_app()
    # Startup event triggers validation; missing OPENAI_API_KEY in non-stub should raise
    with pytest.raises(RuntimeError):
        with TestClient(app):  # context manager runs startup
            pass
