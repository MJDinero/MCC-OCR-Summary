"""Metrics utilities for MCC OCR Summary pipeline."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, ClassVar, Iterator

from prometheus_client import Counter, Histogram
from starlette.responses import PlainTextResponse

from .interfaces import MetricsClient

LOG = logging.getLogger(__name__)


class PrometheusMetrics(MetricsClient):
    """Prometheus-backed metrics client."""

    _LATENCY = Histogram(
        "mcc_pipeline_latency_seconds",
        "Stage latency in seconds",
        ["stage", "name"],
    )
    _COUNTERS = Counter(
        "mcc_pipeline_events_total",
        "Pipeline event counts",
        ["stage", "name"],
    )
    _DEFAULT_INSTANCE: ClassVar["PrometheusMetrics | None"] = None

    def observe_latency(self, name: str, value: float, **labels: str) -> None:
        stage = labels.get("stage", "unknown")
        PrometheusMetrics._LATENCY.labels(stage=stage, name=name).observe(value)

    def increment(self, name: str, amount: int = 1, **labels: str) -> None:
        stage = labels.get("stage", "unknown")
        PrometheusMetrics._COUNTERS.labels(stage=stage, name=name).inc(amount)

    @contextmanager
    def time(self, name: str, **labels: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.observe_latency(name, time.perf_counter() - start, **labels)

    @classmethod
    def default(cls) -> "PrometheusMetrics":
        if cls._DEFAULT_INSTANCE is None:
            cls._DEFAULT_INSTANCE = cls()
        return cls._DEFAULT_INSTANCE

    @classmethod
    def instrument_app(cls, app: Any) -> "PrometheusMetrics":
        """Attach the /metrics endpoint to the FastAPI/Starlette app."""
        metrics = cls.default()
        if getattr(app.state, "_prometheus_instrumented", False):
            return metrics
        try:
            from prometheus_client import CONTENT_TYPE_LATEST, generate_latest  # type: ignore
        except Exception:  # pragma: no cover - optional dependency
            LOG.warning("prometheus_client not installed; metrics endpoint disabled")
            return metrics

        @app.get("/metrics")
        async def _metrics_endpoint():  # pragma: no cover - passthrough
            data = generate_latest()
            return PlainTextResponse(
                data.decode("utf-8"), media_type=CONTENT_TYPE_LATEST
            )

        app.state._prometheus_instrumented = True
        app.state.metrics = metrics
        return metrics


class NullMetrics(MetricsClient):
    """No-op metrics implementation."""

    def observe_latency(self, name: str, value: float, **labels: str) -> None:
        LOG.debug("Metric ignored: %s=%s labels=%s", name, value, labels)

    def increment(self, name: str, amount: int = 1, **labels: str) -> None:
        LOG.debug("Counter ignored: %s+=%s labels=%s", name, amount, labels)


__all__ = ["PrometheusMetrics", "NullMetrics"]
