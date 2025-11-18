"""Test stub for pipeline failure publisher."""

from __future__ import annotations

import logging
from typing import Any

_LOG = logging.getLogger("pipeline_failures")


def publish_pipeline_failure(*args: Any, **kwargs: Any) -> None:
    """Best-effort stub used in local/test environments."""

    _LOG.warning(
        "pipeline_failure_stub",
        extra={"args_len": len(args), "kwargs_keys": sorted(kwargs)},
    )
