"""Startup helpers for Google service account credential hydration."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from src.config import get_config
from src.utils.secrets import resolve_secret_env

_LOG = logging.getLogger(__name__)


def hydrate_google_credentials_file() -> None:
    """Persist service account JSON from env to a filesystem path."""
    target_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    project_id = os.getenv("PROJECT_ID")
    cfg = None
    try:
        cfg = get_config()
    except Exception:  # pragma: no cover - config may not be initialised yet
        cfg = None
    raw_credentials = (cfg.service_account_json if cfg else None) or resolve_secret_env(
        "SERVICE_ACCOUNT_JSON", project_id=project_id
    )

    if not target_path or not raw_credentials:
        return

    trimmed = raw_credentials.strip()
    if not trimmed:
        return

    payload = trimmed
    if not trimmed.startswith(("{", "[")):
        try:
            secret_path = Path(trimmed)
            if secret_path.exists():
                payload = secret_path.read_text(encoding="utf-8")
        except OSError as exc:
            _LOG.warning(
                "SERVICE_ACCOUNT_JSON path could not be read; skipping credential file hydration",
                extra={"error": str(exc)},
            )
            return
    try:
        json.loads(payload)
    except json.JSONDecodeError as exc:
        _LOG.warning(
            "SERVICE_ACCOUNT_JSON is not valid JSON; skipping credential file hydration",
            extra={"error": str(exc)},
        )
        return

    path = Path(target_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
        os.chmod(path, 0o600)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(path)
    except Exception as exc:  # pragma: no cover - defensive logging
        _LOG.error("Failed to materialise GOOGLE_APPLICATION_CREDENTIALS file: %s", exc)
        return


__all__ = ["hydrate_google_credentials_file"]
