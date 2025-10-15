"""Utilities for resolving Secret Manager references at runtime."""

from __future__ import annotations

import os
from typing import Optional

try:  # pragma: no cover - optional dependency for local testing
    from google.cloud import secretmanager  # type: ignore
    from google.api_core import exceptions as gexc  # type: ignore
except Exception:  # pragma: no cover
    secretmanager = None  # type: ignore
    gexc = None  # type: ignore

SM_PREFIX = "sm://"
_SECRET_CACHE: dict[str, str] = {}


class SecretResolutionError(RuntimeError):
    """Raised when a Secret Manager reference cannot be resolved."""


def _normalise_secret_path(raw: str, project_id: str | None) -> str:
    raw = raw.strip()
    if not raw:
        raise SecretResolutionError("Empty secret reference")

    if raw.startswith("projects/"):
        base, sep, version = raw.partition(":")
        if sep and "/versions/" not in base:
            version = version.strip() or "latest"
            if not version.startswith("versions/"):
                version = f"versions/{version}"
            raw = f"{base}/{version}"
        if "/versions/" not in raw:
            raw = f"{raw.rstrip('/')}/versions/latest"
        return raw

    if not project_id:
        raise SecretResolutionError("project_id is required for shorthand sm:// references")
    secret_id, sep, version = raw.partition(":")
    secret_id = secret_id.strip()
    if not secret_id:
        raise SecretResolutionError("Secret identifier missing in sm:// reference")
    version = version.strip()
    version_path = f"versions/{version}" if version else "versions/latest"
    return f"projects/{project_id}/secrets/{secret_id}/{version_path}"


def resolve_secret(value: str | None, *, project_id: str | None = None) -> str | None:
    """Resolve Secret Manager references of the form sm://..."""
    if value is None:
        return None
    if not isinstance(value, str):
        return value

    trimmed = value.strip()
    if not trimmed.startswith(SM_PREFIX):
        return value

    cached = _SECRET_CACHE.get(trimmed)
    if cached is not None:
        return cached

    if secretmanager is None:
        raise SecretResolutionError("google-cloud-secret-manager is not installed")

    secret_path = _normalise_secret_path(trimmed[len(SM_PREFIX) :], project_id)
    try:
        client = secretmanager.SecretManagerServiceClient()
        response = client.access_secret_version(name=secret_path)
    except Exception as exc:  # pragma: no cover - wrapped for consistent error surface
        raise SecretResolutionError(f"Failed to access secret {secret_path}: {exc}") from exc

    payload = getattr(response, "payload", None)
    data: Optional[bytes] = None
    if payload is not None:
        data = getattr(payload, "data", None)
    if data is None:
        raise SecretResolutionError(f"Secret {secret_path} returned no payload data")

    resolved = data.decode("utf-8")
    _SECRET_CACHE[trimmed] = resolved
    return resolved


def resolve_secret_env(
    var_name: str,
    *,
    project_id: str | None = None,
    default: str | None = None,
    required: bool = False,
) -> str | None:
    """Resolve a Secret Manager reference from an environment variable."""
    raw = os.getenv(var_name)
    if raw is None:
        if required:
            raise SecretResolutionError(f"Environment variable {var_name} is required")
        return default
    resolved = resolve_secret(raw, project_id=project_id)
    if required and not resolved:
        raise SecretResolutionError(f"Resolved secret for {var_name} is empty")
    return resolved if resolved is not None else default


def clear_secret_cache() -> None:
    """Clear cached secrets (intended for tests)."""
    _SECRET_CACHE.clear()


__all__ = [
    "SecretResolutionError",
    "resolve_secret",
    "resolve_secret_env",
    "clear_secret_cache",
]
