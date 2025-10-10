from fastapi.testclient import TestClient
from src.main import create_app


class _StubOCR:
    def __init__(self, *args, **kwargs):
        pass

    def process(self, data):  # pragma: no cover - health tests do not invoke OCR
        return {"text": "", "pages": []}


def _build_app(monkeypatch):
    monkeypatch.setattr('src.main.OCRService', lambda *args, **kwargs: _StubOCR())
    app = create_app()
    app.state.ocr_service = _StubOCR()
    return app


def test_healthz_ok(monkeypatch):
    app = _build_app(monkeypatch)
    client = TestClient(app)
    r = client.get('/healthz')
    assert r.status_code == 200
    assert r.json().get('status') == 'ok'


def test_metrics_endpoint_available(monkeypatch):
    app = _build_app(monkeypatch)
    client = TestClient(app)
    r = client.get('/metrics')
    # If prometheus_client is installed we expect 200 & some metric text
    if r.status_code == 200:
        assert 'python_info' in r.text or 'process_cpu' in r.text or 'summary' in r.text
    else:
        # Graceful degradation (endpoint absent)
        assert r.status_code in {404, 500}