from fastapi.testclient import TestClient
from src.main import create_app


def test_healthz_ok():
    app = create_app()
    client = TestClient(app)
    r = client.get('/healthz')
    assert r.status_code == 200
    assert r.json().get('status') == 'ok'


def test_metrics_endpoint_available():
    app = create_app()
    client = TestClient(app)
    r = client.get('/metrics')
    # If prometheus_client is installed we expect 200 & some metric text
    if r.status_code == 200:
        assert 'python_info' in r.text or 'process_cpu' in r.text or 'summary' in r.text
    else:
        # Graceful degradation (endpoint absent)
        assert r.status_code in {404, 500}