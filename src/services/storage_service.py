"""Storage service: persists summaries and metadata to durable storage."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Protocol

from ..models.events import StorageRequestMessage, SummaryResultMessage
from .interfaces import MetricsClient, PubSubPublisher
from .metrics import PrometheusMetrics

LOG = logging.getLogger(__name__)


class SummaryRepository(Protocol):
    """Repository abstraction for persisting summaries to BigQuery / GCS."""

    def write_summary(
        self,
        *,
        job_id: str,
        final_summary: str,
        per_chunk_summaries: list[SummaryResultMessage],
        metadata: dict[str, str],
    ) -> None:
        ...


@dataclass(slots=True)
class StorageConfig:
    output_bucket: str
    bigquery_dataset: str
    bigquery_table: str
    region: str


class StorageService:
    """Consumes summary messages and persists them with idempotency & encryption."""

    def __init__(
        self,
        *,
        repository: SummaryRepository,
        config: StorageConfig,
        publisher: PubSubPublisher,
        dlq_topic: str,
        metrics: MetricsClient | None = None,
    ) -> None:
        self.repository = repository
        self.config = config
        self.publisher = publisher
        self.dlq_topic = dlq_topic
        self.metrics = metrics or PrometheusMetrics.default()

    async def handle_message(self, message: StorageRequestMessage) -> None:
        """Persist aggregated summary and emit metrics."""
        started = time.perf_counter()
        try:
            self.repository.write_summary(
                job_id=message.job_id,
                final_summary=message.final_summary,
                per_chunk_summaries=message.per_chunk_summaries,
                metadata=message.metadata,
            )
            duration = time.perf_counter() - started
            if self.metrics:
                self.metrics.increment("jobs_completed_total", stage="storage")
                self.metrics.observe_latency(
                    "storage_persist_latency_seconds",
                    duration,
                    stage="storage",
                )
            LOG.info(
                "storage_persist_complete",
                extra={
                    "job_id": message.job_id,
                    "trace_id": message.trace_id,
                    "latency_ms": int(duration * 1000),
                    "stage": "storage",
                    "service": "storage_service",
                    "redaction_applied": False,
                },
            )
        except Exception as exc:  # noqa: BLE001
            await self._handle_failure(message, exc, time.perf_counter() - started)
            raise

    async def _handle_failure(
        self,
        message: StorageRequestMessage,
        error: Exception,
        duration: float | None = None,
    ) -> None:
        latency_ms = int(duration * 1000) if duration is not None else None
        LOG.exception(
            "storage_failed",
            extra={
                "job_id": message.job_id,
                "trace_id": message.trace_id,
                "latency_ms": latency_ms,
                "stage": "storage",
                "service": "storage_service",
                "error_type": type(error).__name__,
                "redaction_applied": False,
            },
        )
        payload = {
            "job_id": message.job_id,
            "trace_id": message.trace_id,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "redaction_applied": False,
        }
        await self.publisher.publish(
            self.dlq_topic,
            json.dumps(payload).encode("utf-8"),
            {"job_id": message.job_id, "trace_id": message.trace_id},
        )
        if self.metrics:
            self.metrics.increment("dlq_messages_total", stage="storage")


__all__ = ["StorageService", "StorageConfig", "SummaryRepository"]
