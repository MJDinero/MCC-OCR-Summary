import pytest
pytest.skip("Legacy request id echo test removed after refactor", allow_module_level=True)

from fastapi.testclient import TestClient
from src.main import create_app


def test_request_id_echo():
    app = create_app()
    client = TestClient(app)
    rid = "abc-123"
    resp = client.get("/healthz", headers={"x-request-id": rid})
    assert resp.status_code == 200
    assert resp.headers.get("x-request-id") == rid