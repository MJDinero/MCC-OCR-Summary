"""Metrics utilities for MCC OCR Summary pipeline."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Iterator

from prometheus_client import Counter, Histogram

from .interfaces import MetricsClient

LOG = logging.getLogger(__name__)


class PrometheusMetrics(MetricsClient):
    """Prometheus-backed metrics client."""

    def __init__(self) -> None:
        self.latency = Histogram(
            "mcc_pipeline_latency_seconds",
            "Stage latency in seconds",
            ["stage", "name"],
        )
        self.counters = Counter(
            "mcc_pipeline_events_total",
            "Pipeline event counts",
            ["stage", "name"],
        )

    def observe_latency(self, name: str, value: float, **labels: str) -> None:
        self.latency.labels(stage=labels.get("stage", "unknown"), name=name).observe(value)

    def increment(self, name: str, amount: int = 1, **labels: str) -> None:
        self.counters.labels(stage=labels.get("stage", "unknown"), name=name).inc(amount)

    @contextmanager
    def time(self, name: str, **labels: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.observe_latency(name, time.perf_counter() - start, **labels)


class NullMetrics(MetricsClient):
    """No-op metrics implementation."""

    def observe_latency(self, name: str, value: float, **labels: str) -> None:
        LOG.debug("Metric ignored: %s=%s labels=%s", name, value, labels)

    def increment(self, name: str, amount: int = 1, **labels: str) -> None:
        LOG.debug("Counter ignored: %s+=%s labels=%s", name, amount, labels)


__all__ = ["PrometheusMetrics", "NullMetrics"]
