from __future__ import annotations

import io
import json
import logging
from typing import List

from src.logging_setup import JsonFormatter, configure_logging, set_request_id
from src.utils.logging_utils import stage_marker, structured_log


def test_configure_logging_installs_json_formatter():
    root = logging.getLogger()
    original_handlers: List[logging.Handler] = list(root.handlers)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    try:
        configure_logging()
        assert root.handlers, "configure_logging should attach a stream handler"
        handler = root.handlers[0]
        formatter = handler.formatter
        set_request_id("req-123")
        record = logging.LogRecord(
            name="test-logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=20,
            msg="hello world",
            args=(),
            exc_info=None,
        )
        payload = json.loads(formatter.format(record))
        assert payload["logger"] == "test-logger"
        assert payload["request_id"] == "req-123"
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in original_handlers:
            root.addHandler(handler)
        set_request_id(None)


def test_structured_log_allowlist_filters_unknown_fields():
    logger = logging.getLogger("structured-log-test")
    original_handlers: List[logging.Handler] = list(logger.handlers)
    original_propagate = logger.propagate
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(JsonFormatter())
    try:
        for existing in list(logger.handlers):
            logger.removeHandler(existing)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        structured_log(
            logger,
            logging.INFO,
            "unit_event",
            stage="ocr",
            status="started",
            trace_id="t-123",
            request_id="req-456",
            text_length=120,
            disallowed_field="secret-value",
        )
        handler.flush()
        payload = json.loads(buffer.getvalue().strip())
        assert payload["stage"] == "ocr"
        assert payload["trace_id"] == "t-123"
        assert payload["request_id"] == "req-456"
        assert "disallowed_field" not in payload
    finally:
        logger.removeHandler(handler)
        for existing in original_handlers:
            logger.addHandler(existing)
        logger.propagate = original_propagate


def test_stage_marker_emits_start_and_completion_records():
    logger = logging.getLogger("stage-marker-test")
    original_handlers: List[logging.Handler] = list(logger.handlers)
    original_propagate = logger.propagate
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(JsonFormatter())
    try:
        for existing in list(logger.handlers):
            logger.removeHandler(existing)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        with stage_marker(
            logger,
            stage="summarisation",
            trace_id="trace-789",
            request_id="req-000",
        ):
            pass
        handler.flush()
        entries = [
            json.loads(line)
            for line in buffer.getvalue().splitlines()
            if line.strip()
        ]
        stages = [entry for entry in entries if entry.get("event") == "pipeline_stage"]
        assert [entry["status"] for entry in stages] == ["started", "completed"]
        assert stages[-1]["duration_ms"] >= 0
        assert stages[-1]["stage"] == "summarisation"
    finally:
        logger.removeHandler(handler)
        for existing in original_handlers:
            logger.addHandler(existing)
        logger.propagate = original_propagate
