"""Logging filter that redacts PHI/PII before records reach handlers."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Iterable, Sequence

from src.utils import redact

_LOG_RECORD_FIELDS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
}


class PHIRedactFilter(logging.Filter):
    """Scrub PHI/PII tokens from log messages, args, and structured extras."""

    def __init__(
        self,
        name: str | None = None,
        *,
        patterns: Iterable[Any] | None = None,
        replacement: str = redact.REDACTION_TOKEN,
    ) -> None:
        super().__init__(name or "")
        self._patterns = tuple(patterns or redact.DEFAULT_PATTERNS)
        self._replacement = replacement

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        record.msg = self._scrub(record.msg)
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(self._scrub(arg) for arg in record.args)
            elif isinstance(record.args, Mapping):
                record.args = {k: self._scrub(v) for k, v in record.args.items()}
            else:
                record.args = self._scrub(record.args)

        for key, value in list(record.__dict__.items()):
            if key in _LOG_RECORD_FIELDS or key.startswith("_"):
                continue
            record.__dict__[key] = self._scrub(value)
        return True

    def _scrub(self, value: Any) -> Any:
        if isinstance(value, str):
            return redact.redact_text(value, patterns=self._patterns, replacement=self._replacement)
        if isinstance(value, Mapping):
            return redact.redact_mapping(value, patterns=self._patterns, replacement=self._replacement)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            if isinstance(value, tuple):
                return tuple(self._scrub(item) for item in value)
            return [self._scrub(item) for item in value]
        if isinstance(value, set):
            return {self._scrub(item) for item in value}
        return value


__all__ = ["PHIRedactFilter"]
