"""Utility helpers to scrub PHI/PII from logs and diagnostics."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

DEFAULT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # SSN
    re.compile(r"\b\d{2}-\d{7}\b"),  # MRN-like
    re.compile(
        r"\b\d{8,}\b"
    ),  # generic long identifiers (account numbers, phone digits)
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),  # email
    re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"),  # phone number
)

REDACTION_TOKEN = "[REDACTED]"


def redact_text(
    value: str,
    *,
    patterns: Iterable[re.Pattern[str]] | None = None,
    replacement: str = REDACTION_TOKEN,
) -> str:
    """Redact known PHI/PII elements from text payloads."""
    compiled = tuple(patterns or DEFAULT_PATTERNS)
    scrubbed = value
    for pattern in compiled:
        scrubbed = pattern.sub(replacement, scrubbed)
    return scrubbed


def redact_mapping(
    payload: Mapping[str, Any],
    *,
    patterns: Iterable[re.Pattern[str]] | None = None,
    replacement: str = REDACTION_TOKEN,
) -> dict[str, Any]:
    """Recursively redact mapping values."""
    result: dict[str, Any] = {}
    compiled = tuple(patterns or DEFAULT_PATTERNS)
    for key, value in payload.items():
        result[key] = _redact_value(value, compiled, replacement)
    return result


def _redact_value(
    value: Any,
    patterns: tuple[re.Pattern[str], ...],
    replacement: str,
) -> Any:
    if isinstance(value, str):
        return redact_text(value, patterns=patterns, replacement=replacement)
    if isinstance(value, Mapping):
        return {k: _redact_value(v, patterns, replacement) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(item, patterns, replacement) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item, patterns, replacement) for item in value)
    return value


__all__ = ["redact_text", "redact_mapping", "REDACTION_TOKEN"]
