from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.utils import secrets


def test_normalise_secret_path_variants():
    assert secrets._normalise_secret_path("projects/demo/secrets/key", project_id=None) == (
        "projects/demo/secrets/key/versions/latest"
    )
    path = secrets._normalise_secret_path("token:5", project_id="demo")
    assert path == "projects/demo/secrets/token/versions/5"
    with pytest.raises(secrets.SecretResolutionError):
        secrets._normalise_secret_path("", project_id="demo")
    with pytest.raises(secrets.SecretResolutionError):
        secrets._normalise_secret_path("token", project_id=None)


def test_resolve_secret_uses_cache(monkeypatch):
    secrets.clear_secret_cache()
    calls: list[str] = []

    class _StubClient:
        def access_secret_version(self, name: str):
            calls.append(name)
            return SimpleNamespace(payload=SimpleNamespace(data=b"secret-value"))

    monkeypatch.setattr(
        secrets,
        "secretmanager",
        SimpleNamespace(SecretManagerServiceClient=lambda: _StubClient()),
    )
    value = secrets.resolve_secret("sm://api-key", project_id="demo")
    assert value == "secret-value"
    # cached call should not invoke client again
    cached = secrets.resolve_secret("sm://api-key", project_id="demo")
    assert cached == "secret-value"
    assert len(calls) == 1


def test_resolve_secret_env_required(monkeypatch):
    monkeypatch.delenv("MISSING_SECRET", raising=False)
    with pytest.raises(secrets.SecretResolutionError):
        secrets.resolve_secret_env("MISSING_SECRET", required=True)
    monkeypatch.setenv("OPTIONAL_SECRET", "plain-text")
    assert secrets.resolve_secret_env("OPTIONAL_SECRET") == "plain-text"
