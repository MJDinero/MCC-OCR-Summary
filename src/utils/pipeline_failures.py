"""Minimal stub for pipeline failure publishing (no-op in local mode)."""
from __future__ import annotations

import logging
from typing import Any

_LOG = logging.getLogger("pipeline_failures")


def publish_pipeline_failure(**payload: Any) -> None:
    """Best-effort logger for pipeline failure events."""
    _LOG.warning("pipeline_failure", extra=payload)
