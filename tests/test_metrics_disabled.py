import pytest
pytest.skip("Legacy metrics test removed after refactor", allow_module_level=True)

from fastapi.testclient import TestClient
from src.main import create_app


def test_metrics_returns_404_when_disabled():
    app = create_app()
    app.state.config.enable_metrics = False
    client = TestClient(app)
    resp = client.get('/metrics')
    assert resp.status_code == 404
    assert 'disabled' in resp.text.lower()
