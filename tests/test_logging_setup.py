from __future__ import annotations

import json
import logging
from typing import List

from src.logging_setup import configure_logging, set_request_id


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
