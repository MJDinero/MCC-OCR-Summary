"""Utilities for publishing pipeline failures to DLQ destinations."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict

from src.config import get_config

try:  # pragma: no cover - optional metrics
    from prometheus_client import Counter  # type: ignore

    _PIPELINE_FAILURES = Counter(
        "pipeline_failures_total",
        "Total pipeline failures published to DLQ",
        ["stage", "status"],
    )
except Exception:  # pragma: no cover
    _PIPELINE_FAILURES = None  # type: ignore

_LOG = logging.getLogger("pipeline_failures")
_PUBLISHER_CLIENT = None
_STAGE_TOPIC_MAP = (
    ("DOC_AI", "ocr_dlq_topic"),
    ("SUMMARY", "summary_dlq_topic"),
    ("SUPERVISOR", "summary_dlq_topic"),
    ("PDF", "storage_dlq_topic"),
    ("STORAGE", "storage_dlq_topic"),
)


def _get_publisher():
    global _PUBLISHER_CLIENT  # pylint: disable=global-statement
    if _PUBLISHER_CLIENT is None:
        from google.cloud import pubsub_v1  # type: ignore

        _PUBLISHER_CLIENT = pubsub_v1.PublisherClient()
    return _PUBLISHER_CLIENT


def _resolve_topic(stage: str | None, cfg: Any) -> str | None:
    if not stage:
        return None
    upper_stage = stage.upper()
    for needle, attr in _STAGE_TOPIC_MAP:
        if needle in upper_stage:
            return getattr(cfg, attr, None)
    return None


def publish_pipeline_failure(
    *,
    job_id: str | None = None,
    stage: str | None = None,
    error: Exception | str | None = None,
    trace_id: str | None = None,
    metadata: Dict[str, Any] | None = None,
    context: Dict[str, Any] | None = None,
) -> bool:
    """Publish a failure event to the configured DLQ destination."""

    payload = {
        "job_id": job_id,
        "stage": stage,
        "trace_id": trace_id,
        "error": str(error) if error is not None else None,
        "metadata": metadata or context or {},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    cfg = get_config()
    topic = _resolve_topic(stage, cfg)
    if not topic:
        _LOG.warning(
            "pipeline_failure_no_topic",
            extra={"stage": stage, "payload": payload},
        )
        if _PIPELINE_FAILURES:
            _PIPELINE_FAILURES.labels(stage=stage or "unknown", status="missing-topic").inc()
        return False
    try:
        publisher = _get_publisher()
        future = publisher.publish(
            topic,
            json.dumps(payload).encode("utf-8"),
            stage=(stage or "unknown"),
        )
        future.result(timeout=10)
        _LOG.info(
            "pipeline_failure_published",
            extra={"stage": stage, "topic": topic, "job_id": job_id, "trace_id": trace_id},
        )
        if _PIPELINE_FAILURES:
            _PIPELINE_FAILURES.labels(stage=stage or "unknown", status="published").inc()
        return True
    except Exception as exc:  # pragma: no cover - network/environment issues
        _LOG.exception(
            "pipeline_failure_publish_failed",
            extra={"stage": stage, "topic": topic, "job_id": job_id, "error": str(exc)},
        )
        if _PIPELINE_FAILURES:
            _PIPELINE_FAILURES.labels(stage=stage or "unknown", status="error").inc()
        return False
