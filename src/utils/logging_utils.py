"""Helpers for emitting consistent structured logs."""

from __future__ import annotations

import logging
from typing import Any, Dict


def structured_log(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    """Emit a log record with an `event` attribute and structured extras."""
    payload: Dict[str, Any] = {"event": event}
    payload.update(fields)
    logger.log(level, event, extra=payload)


__all__ = ["structured_log"]
