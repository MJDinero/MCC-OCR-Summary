from types import SimpleNamespace

import pytest

from src.utils import secrets as secrets_mod
from src.utils.secrets import SecretResolutionError, resolve_secret, resolve_secret_env


class _FakeClient:
    def __init__(self):
        self.calls = []

    def access_secret_version(self, name):
        self.calls.append(name)
        return SimpleNamespace(payload=SimpleNamespace(data=b"resolved-value"))


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    secrets_mod.clear_secret_cache()
    yield
    secrets_mod.clear_secret_cache()


def test_resolve_secret_no_prefix_returns_value():
    assert resolve_secret("plain-value", project_id="proj") == "plain-value"


def test_resolve_secret_shorthand(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(secrets_mod, "secretmanager", SimpleNamespace(SecretManagerServiceClient=lambda: client))
    value = resolve_secret("sm://api-token", project_id="proj")
    assert value == "resolved-value"
    assert client.calls == ["projects/proj/secrets/api-token/versions/latest"]


def test_resolve_secret_full_path(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(secrets_mod, "secretmanager", SimpleNamespace(SecretManagerServiceClient=lambda: client))
    uri = "sm://projects/demo/secrets/service-key:42"
    value = resolve_secret(uri, project_id=None)
    assert value == "resolved-value"
    assert client.calls == ["projects/demo/secrets/service-key/versions/42"]


def test_resolve_secret_env_required(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(secrets_mod, "secretmanager", SimpleNamespace(SecretManagerServiceClient=lambda: client))
    monkeypatch.setenv("SECRET_EXAMPLE", "sm://token")
    value = resolve_secret_env("SECRET_EXAMPLE", project_id="proj", required=True)
    assert value == "resolved-value"


def test_resolve_secret_missing_project_raises(monkeypatch):
    monkeypatch.setattr(secrets_mod, "secretmanager", SimpleNamespace(SecretManagerServiceClient=lambda: _FakeClient()))
    with pytest.raises(SecretResolutionError):
        resolve_secret("sm://token")


def test_resolve_secret_env_missing_required(monkeypatch):
    with pytest.raises(SecretResolutionError):
        resolve_secret_env("DOES_NOT_EXIST", required=True)
