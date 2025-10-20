"""Startup helpers for Google service account credential hydration."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

_LOG = logging.getLogger(__name__)


def hydrate_google_credentials_file() -> None:
    """Persist service account JSON from env to a filesystem path."""
    target_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    raw_credentials = os.getenv("SERVICE_ACCOUNT_JSON")

    if not target_path or not raw_credentials:
        return

    trimmed = raw_credentials.strip()
    if not trimmed:
        return

    try:
        json.loads(trimmed)
    except json.JSONDecodeError:
        _LOG.warning("SERVICE_ACCOUNT_JSON is not valid JSON; skipping credential file hydration")
        return

    path = Path(target_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(trimmed, encoding="utf-8")
        os.chmod(path, 0o600)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(path)
    except Exception as exc:  # pragma: no cover - defensive logging
        _LOG.error("Failed to materialise GOOGLE_APPLICATION_CREDENTIALS file: %s", exc)
        return
    finally:
        os.environ.pop("SERVICE_ACCOUNT_JSON", None)


__all__ = ["hydrate_google_credentials_file"]
