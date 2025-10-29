"""Helpers for publishing pipeline failure events to the DLQ."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

try:  # pragma: no cover - optional dependency in tests
    from google.cloud import pubsub_v1  # type: ignore
except Exception:  # pragma: no cover - allow unit tests without Pub/Sub
    pubsub_v1 = None  # type: ignore

_LOG = logging.getLogger("pipeline.dlq")
_PUBLISHER: "pubsub_v1.PublisherClient | None" = None


def _get_publisher() -> "pubsub_v1.PublisherClient | None":
    global _PUBLISHER  # pylint: disable=global-statement
    if _PUBLISHER is not None or pubsub_v1 is None:
        return _PUBLISHER
    try:
        _PUBLISHER = pubsub_v1.PublisherClient()
    except Exception as exc:  # pragma: no cover - only logged
        _LOG.warning("dlq_publisher_init_failed", extra={"error": str(exc)})
        _PUBLISHER = None
    return _PUBLISHER


def publish_pipeline_failure(
    *,
    stage: str,
    job_id: str | None,
    trace_id: str | None = None,
    error: Exception | str | None = None,
    metadata: dict[str, Any] | None = None,
    dlq_topic: str | None = None,
) -> None:
    """Publish a pipeline failure payload to the configured DLQ topic."""

    if not job_id:
        return
    topic = dlq_topic or os.getenv("PIPELINE_DLQ_TOPIC")
    if not topic:
        return
    publisher = _get_publisher()
    if publisher is None:
        return

    error_message = str(error) if error is not None else ""
    payload = {
        "stage": stage,
        "job_id": job_id,
        "trace_id": trace_id,
        "error": {
            "type": type(error).__name__ if isinstance(error, Exception) else None,
            "message": error_message,
        },
        "metadata": metadata or {},
    }
    attributes = {"job_id": job_id, "stage": stage}
    if trace_id:
        attributes["trace_id"] = trace_id
    try:
        publisher.publish(topic, json.dumps(payload).encode("utf-8"), **attributes)
    except Exception as exc:  # pragma: no cover - best effort logging
        _LOG.warning(
            "dlq_publish_failed",
            extra={"error": str(exc), "stage": stage, "job_id": job_id, "trace_id": trace_id},
        )


__all__ = ["publish_pipeline_failure"]
