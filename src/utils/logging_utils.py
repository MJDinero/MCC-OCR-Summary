"""Helpers for emitting consistent structured logs and stage telemetry."""

from __future__ import annotations

import logging
import time
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from typing import Any, Dict, Literal

STRUCTURED_LOG_ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "alignment_ok",
        "bytes",
        "case_id_non_phi",
        "cleaned_text_length",
        "component",
        "content_alignment",
        "debug_enabled",
        "doc_stats",
        "drive_file_id",
        "duration_ms",
        "error",
        "error_type",
        "event",
        "file_id",
        "folder_id",
        "headers",
        "length_ok",
        "length_score",
        "min_required",
        "multi_pass_required",
        "ocr_length",
        "pages",
        "paragraphs",
        "pdf_bytes",
        "ratio_ok",
        "reason",
        "report_name",
        "request_id",
        "retries",
        "semantic_ok",
        "skip_reason",
        "source",
        "stage",
        "status",
        "supervisor_passed",
        "summary_chars",
        "summary_length",
        "structure_ok",
        "text_length",
        "trace_id",
    }
)


def _filter_structured_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in fields.items()
        if key in STRUCTURED_LOG_ALLOWED_FIELDS and value is not None
    }


def structured_log(
    logger: logging.Logger, level: int, event: str, **fields: Any
) -> None:
    """Emit a log record with an `event` attribute and structured extras."""
    payload: Dict[str, Any] = {"event": event, "_structured_log": True}
    payload.update(_filter_structured_fields(fields))
    logger.log(level, event, extra=payload)


class StageMarker(
    AbstractContextManager["StageMarker"], AbstractAsyncContextManager["StageMarker"]
):
    """Context manager that emits stage start/completion telemetry."""

    def __init__(
        self,
        logger: logging.Logger,
        *,
        stage: str,
        level: int = logging.INFO,
        **base_fields: Any,
    ) -> None:
        self._logger = logger
        self._stage = stage
        merged_fields = {"stage": stage}
        merged_fields.update(base_fields)
        self._base_fields: Dict[str, Any] = _filter_structured_fields(merged_fields)
        self._level = level
        self._started_at: float | None = None
        self._completion_fields: Dict[str, Any] = {}

    def add_completion_fields(self, **fields: Any) -> None:
        """Record safe metrics to append when the stage finishes."""
        self._completion_fields.update(_filter_structured_fields(fields))

    def _start(self) -> None:
        self._started_at = time.perf_counter()
        structured_log(
            self._logger,
            self._level,
            "pipeline_stage",
            status="started",
            **self._base_fields,
        )

    def _finish(self, exc: BaseException | None) -> None:
        now = time.perf_counter()
        duration_ms = (
            int((now - self._started_at) * 1000) if self._started_at is not None else 0
        )
        payload = dict(self._base_fields)
        payload.update(self._completion_fields)
        payload["duration_ms"] = duration_ms
        if exc:
            payload["status"] = "failed"
            payload["error_type"] = exc.__class__.__name__
            structured_log(self._logger, logging.ERROR, "pipeline_stage", **payload)
        else:
            payload["status"] = "completed"
            structured_log(self._logger, logging.INFO, "pipeline_stage", **payload)

    def __enter__(self) -> "StageMarker":
        self._start()
        return self

    def __exit__(
        self,
        exc_type,
        exc: BaseException | None,
        _tb,
    ) -> Literal[False]:
        self._finish(exc)
        return False

    async def __aenter__(self) -> "StageMarker":
        self._start()
        return self

    async def __aexit__(
        self,
        exc_type,
        exc: BaseException | None,
        _tb,
    ) -> Literal[False]:
        self._finish(exc)
        return False


def stage_marker(
    logger: logging.Logger, *, stage: str, level: int = logging.INFO, **fields: Any
) -> StageMarker:
    """Convenience helper mirroring `with stage_marker(...)` usage."""
    return StageMarker(logger, stage=stage, level=level, **fields)


def log_stage_skipped(
    logger: logging.Logger,
    *,
    stage: str,
    reason: str,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """Emit a deterministic record when a stage is bypassed."""
    payload = {"stage": stage, "status": "skipped", "skip_reason": reason}
    payload.update(fields)
    structured_log(logger, level, "pipeline_stage", **payload)


__all__ = [
    "StageMarker",
    "log_stage_skipped",
    "stage_marker",
    "structured_log",
    "STRUCTURED_LOG_ALLOWED_FIELDS",
]
