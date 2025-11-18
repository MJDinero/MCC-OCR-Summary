from __future__ import annotations

import json
from src import startup


def test_hydrate_google_credentials_writes_json(tmp_path, monkeypatch):
    target = tmp_path / "creds.json"
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(target))
    monkeypatch.setenv("SERVICE_ACCOUNT_JSON", '{"type":"service_account"}')
    startup.hydrate_google_credentials_file()
    assert target.exists()
    payload = json.loads(target.read_text())
    assert payload["type"] == "service_account"


def test_hydrate_google_credentials_supports_path_source(tmp_path, monkeypatch):
    source = tmp_path / "source.json"
    source.write_text('{"type":"service_account","project_id":"demo"}')
    target = tmp_path / "hydrated.json"
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(target))
    monkeypatch.setenv("SERVICE_ACCOUNT_JSON", str(source))
    startup.hydrate_google_credentials_file()
    data = json.loads(target.read_text())
    assert data["project_id"] == "demo"


def test_hydrate_google_credentials_skips_invalid_json(tmp_path, monkeypatch, caplog):
    target = tmp_path / "creds.json"
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(target))
    monkeypatch.setenv("SERVICE_ACCOUNT_JSON", "not-json")
    with caplog.at_level("WARNING"):
        startup.hydrate_google_credentials_file()
    assert "not valid JSON" in caplog.text
    assert not target.exists()


def test_hydrate_google_credentials_ignores_blank_payload(tmp_path, monkeypatch):
    target = tmp_path / "creds.json"
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(target))
    monkeypatch.setenv("SERVICE_ACCOUNT_JSON", "   ")
    startup.hydrate_google_credentials_file()
    assert not target.exists()


def test_hydrate_google_credentials_handles_read_errors(tmp_path, monkeypatch, caplog):
    target = tmp_path / "creds.json"
    missing = tmp_path / "secret_dir"
    missing.mkdir()
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(target))
    monkeypatch.setenv("SERVICE_ACCOUNT_JSON", str(missing))
    with caplog.at_level("WARNING"):
        startup.hydrate_google_credentials_file()
    assert "path could not be read" in caplog.text
    assert not target.exists()
