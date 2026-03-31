"""Helpers for redacting untrusted failure details from logs and status payloads."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

REDACTED_FAILURE_MESSAGE = "Failure details redacted"
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9._/@:+#=-]{1,160}$")
_SAFE_URI_PREFIXES = ("gs://", "http://", "https://", "projects/")
_EXCEPTION_TYPE_RE = re.compile(r"^[A-Za-z0-9_.]+(?:Error|Exception|Warning)?(?:: .*)?$")


def error_fingerprint(*parts: str | None) -> str | None:
    """Return a short stable hash for correlating a redacted failure."""

    seed = "|".join(
        part.strip() for part in parts if isinstance(part, str) and part.strip()
    )
    if not seed:
        return None
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def sanitize_diagnostic_value(value: Any) -> Any:
    """Preserve safe diagnostic identifiers while redacting free-form text."""

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        token = value.strip()
        if not token:
            return None
        if token.startswith(_SAFE_URI_PREFIXES):
            return token
        if "\n" not in token and _SAFE_TOKEN_RE.fullmatch(token):
            return token
        return "[redacted]"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"error", "message", "detail", "details", "reason"}:
                continue
            safe_value = sanitize_diagnostic_value(item)
            if safe_value is not None:
                result[str(key)] = safe_value
        return result or None
    if isinstance(value, list):
        items = [sanitize_diagnostic_value(item) for item in value]
        filtered = [item for item in items if item is not None]
        return filtered or None
    if isinstance(value, tuple):
        tuple_items = tuple(
            item
            for item in (sanitize_diagnostic_value(entry) for entry in value)
            if item is not None
        )
        return tuple_items or None
    return sanitize_diagnostic_value(str(value))


def sanitize_exception_trace(formatted: str) -> str:
    """Drop exception message bodies while preserving traceback frames."""

    if not formatted:
        return formatted
    redacted_lines: list[str] = []
    for line in formatted.splitlines():
        if line.startswith("Traceback") or line.startswith("  File "):
            redacted_lines.append(line)
            continue
        if line.startswith(
            (
                "During handling of the above exception",
                "The above exception was the direct cause",
            )
        ):
            redacted_lines.append(line)
            continue
        prefix, separator, _rest = line.partition(": ")
        if separator and prefix and _EXCEPTION_TYPE_RE.match(line):
            redacted_lines.append(prefix)
            continue
    return "\n".join(redacted_lines)


def sanitize_failure_details(
    *,
    stage: str | None,
    message: str | None,
    extra: Mapping[str, Any] | None = None,
    last_error: Mapping[str, Any] | None = None,
) -> tuple[str | None, dict[str, Any] | None, dict[str, Any] | None]:
    """Redact arbitrary failure text while preserving diagnostic metadata."""

    error_type: str | None = None
    raw_message: str | None = None
    resolved_stage: str | None = None

    for payload in (last_error, extra):
        if not isinstance(payload, Mapping):
            continue
        payload_stage = payload.get("stage")
        if resolved_stage is None and isinstance(payload_stage, str) and payload_stage:
            resolved_stage = payload_stage
        payload_type = payload.get("error_type")
        if error_type is None and isinstance(payload_type, str) and payload_type.strip():
            error_type = payload_type.strip()
        if raw_message is None:
            for key in ("error", "message", "detail", "details", "reason"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    raw_message = value.strip()
                    break

    if raw_message is None and isinstance(message, str) and message.strip():
        raw_message = message.strip()
    if resolved_stage is None:
        resolved_stage = stage

    fingerprint = error_fingerprint(resolved_stage, error_type, raw_message)

    safe_extra = sanitize_diagnostic_value(extra) if extra is not None else None
    safe_extra_dict = (
        dict(safe_extra)
        if isinstance(safe_extra, dict)
        else {}
    )
    if error_type:
        safe_extra_dict["error_type"] = error_type
    if fingerprint:
        safe_extra_dict["error_fingerprint"] = fingerprint
    if raw_message:
        safe_extra_dict["error_redacted"] = True

    safe_last_error = (
        dict(sanitize_diagnostic_value(last_error))
        if isinstance(sanitize_diagnostic_value(last_error), dict)
        else {}
    )
    if resolved_stage:
        safe_last_error["stage"] = resolved_stage
    if raw_message:
        safe_last_error["error"] = REDACTED_FAILURE_MESSAGE
        safe_last_error["error_redacted"] = True
    if error_type:
        safe_last_error["error_type"] = error_type
    if fingerprint:
        safe_last_error["error_fingerprint"] = fingerprint

    safe_message = REDACTED_FAILURE_MESSAGE if raw_message or message else None
    return (
        safe_message,
        safe_extra_dict or None,
        safe_last_error or None,
    )


__all__ = [
    "REDACTED_FAILURE_MESSAGE",
    "error_fingerprint",
    "sanitize_diagnostic_value",
    "sanitize_exception_trace",
    "sanitize_failure_details",
]
