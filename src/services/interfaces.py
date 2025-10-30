"""Shared interfaces used across MCC OCR Summary services."""

from __future__ import annotations

from typing import Protocol


class PubSubPublisher(Protocol):
    """Abstraction over Pub/Sub publishing for dependency injection."""

    async def publish(
        self,
        topic: str,
        data: bytes,
        attributes: dict[str, str] | None = None,
    ) -> str: ...


class MetricsClient(Protocol):
    """Interface for emitting metrics to Cloud Monitoring or Prometheus."""

    def observe_latency(self, name: str, value: float, **labels: str) -> None: ...

    def increment(self, name: str, amount: int = 1, **labels: str) -> None: ...


__all__ = ["PubSubPublisher", "MetricsClient"]
