"""Structured logging configuration.

Provides a JSON formatter and a request_id context variable. The FastAPI app
should call `configure_logging()` at startup. Use `with request_context(id)` or
`set_request_id(id)` to propagate correlation across inner service calls.
"""
from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        data: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = request_id_var.get()
        if rid:
            data["request_id"] = rid
        if record.exc_info:
            data["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if any(isinstance(h, logging.StreamHandler) for h in root.handlers):  # already configured
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.setLevel(level)
    root.addHandler(handler)


def set_request_id(rid: str | None) -> None:
    request_id_var.set(rid)


__all__ = ["configure_logging", "set_request_id", "request_id_var"]
