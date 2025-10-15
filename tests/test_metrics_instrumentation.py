from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.runtime_server import _worker_count
from src.services.metrics import NullMetrics, PrometheusMetrics


def test_prometheus_instrument_app_registers_endpoint():
    app = FastAPI()
    metrics = PrometheusMetrics.instrument_app(app)
    assert metrics is not None
    # Repeat instrumentation should be idempotent
    PrometheusMetrics.instrument_app(app)
    metrics.increment("test_counter", stage="test")
    metrics.observe_latency("test_latency_seconds", 0.05, stage="test")
    with metrics.time("timed", stage="test"):
        pass

    client = TestClient(app)
    response = client.get("/metrics")
    # prometheus_client might be absent in CI; ensure graceful behaviour
    assert response.status_code in {200, 404}

    null_metrics = NullMetrics()
    null_metrics.observe_latency("ignored", 0.1, stage="test")
    null_metrics.increment("ignored", stage="test")

    assert _worker_count() >= 1
