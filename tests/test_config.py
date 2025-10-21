import os
from types import SimpleNamespace

import pytest

from src.config import AppConfig
from src.utils import secrets as secrets_mod


REQUIRED_KEYS = [
    'PROJECT_ID',
    'REGION',
    'DOC_AI_PROCESSOR_ID',
    'OPENAI_API_KEY',
    'DRIVE_INPUT_FOLDER_ID',
    'DRIVE_REPORT_FOLDER_ID',
    'DRIVE_IMPERSONATION_USER',
    'CMEK_KEY_NAME',
    'GOOGLE_APPLICATION_CREDENTIALS',
]


def _clear():
    for k in REQUIRED_KEYS:
        os.environ.pop(k, None)
    os.environ.pop('SERVICE_ACCOUNT_JSON', None)


def _write_creds(tmp_path):
    path = tmp_path / 'svc.json'
    path.write_text(
        (
            '{'
            '"type":"service_account",'
            '"project_id":"test-project",'
            '"private_key_id":"fake",'
            '"private_key":"-----BEGIN PRIVATE KEY-----\\nFAKE\\n-----END PRIVATE KEY-----\\n",'
            '"client_email":"svc@example.com",'
            '"client_id":"1234567890",'
            '"token_uri":"https://oauth2.googleapis.com/token"'
            '}'
        ),
        encoding='utf-8',
    )
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(path)
    return path


def _populate_required_env(tmp_path, **overrides):
    cred_path = _write_creds(tmp_path)
    env = {
        'PROJECT_ID': 'p',
        'REGION': 'us',
        'DOC_AI_PROCESSOR_ID': 'proc',
        'OPENAI_API_KEY': 'k',
        'DRIVE_INPUT_FOLDER_ID': 'in',
        'DRIVE_REPORT_FOLDER_ID': 'out',
        'DRIVE_IMPERSONATION_USER': 'impersonation@example.com',
        'CMEK_KEY_NAME': 'projects/p/locations/us/keyRings/k/cryptoKeys/key',
    }
    env.update(overrides)
    os.environ.update(env)
    return cred_path


def test_config_missing_required(tmp_path):
    _clear()
    try:
        os.environ['PROJECT_ID'] = 'p'
        _write_creds(tmp_path)
        cfg = AppConfig()
        with pytest.raises(RuntimeError):
            cfg.validate_required()
    finally:
        _clear()


def test_config_success(tmp_path):
    _clear()
    try:
        cred_path = _populate_required_env(tmp_path)
        cfg = AppConfig()
        cfg.validate_required()  # no exception
        assert cfg.project_id == 'p'
        assert cfg.google_application_credentials == str(cred_path)
    finally:
        _clear()


def test_config_resolves_secret(monkeypatch, tmp_path):
    _clear()
    try:
        _populate_required_env(
            tmp_path,
            PROJECT_ID='proj',
            DOC_AI_PROCESSOR_ID='sm://doc-proc',
            OPENAI_API_KEY='sm://openai',
            DRIVE_INPUT_FOLDER_ID='sm://drive-in',
            DRIVE_REPORT_FOLDER_ID='sm://drive-out',
            DRIVE_IMPERSONATION_USER='sm://impersonate',
            CMEK_KEY_NAME='sm://cmek',
        )
        client = SimpleNamespace(access_secret_version=lambda name: SimpleNamespace(payload=SimpleNamespace(data=b"resolved")))
        monkeypatch.setattr(secrets_mod, "secretmanager", SimpleNamespace(SecretManagerServiceClient=lambda: client))
        cfg = AppConfig()
        assert cfg.doc_ai_processor_id == 'resolved'
        assert cfg.openai_api_key == 'resolved'
        assert cfg.drive_impersonation_user == 'resolved'
    finally:
        _clear()


def test_validate_required_missing_credential_file(tmp_path):
    _clear()
    try:
        _populate_required_env(tmp_path)
        missing_path = tmp_path / 'missing.json'
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(missing_path)
        cfg = AppConfig()
        with pytest.raises(RuntimeError, match="file not found"):
            cfg.validate_required()
    finally:
        _clear()


def test_validate_required_invalid_credentials_json(tmp_path):
    _clear()
    try:
        _populate_required_env(tmp_path)
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = '{invalid'
        cfg = AppConfig()
        with pytest.raises(RuntimeError, match="invalid JSON"):
            cfg.validate_required()
    finally:
        _clear()


def test_validate_required_invalid_impersonation(tmp_path):
    _clear()
    try:
        _populate_required_env(tmp_path, DRIVE_IMPERSONATION_USER='invalid-email')
        cfg = AppConfig()
        with pytest.raises(RuntimeError, match="valid email"):
            cfg.validate_required()
    finally:
        _clear()
