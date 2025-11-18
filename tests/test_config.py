import os
from types import SimpleNamespace

import pytest

from src.config import AppConfig
from src.utils import secrets as secrets_mod


REQUIRED_KEYS = [
    "PROJECT_ID",
    "REGION",
    "DOC_AI_PROCESSOR_ID",
    "OPENAI_API_KEY",
    "DRIVE_INPUT_FOLDER_ID",
    "DRIVE_REPORT_FOLDER_ID",
    "INTAKE_GCS_BUCKET",
    "OUTPUT_GCS_BUCKET",
    "SUMMARY_BUCKET",
    "INTERNAL_EVENT_TOKEN",
]


def _clear():
    for k in REQUIRED_KEYS:
        os.environ.pop(k, None)


def test_config_missing_required():
    _clear()
    os.environ["PROJECT_ID"] = "p"
    cfg = AppConfig()
    with pytest.raises(RuntimeError):
        cfg.validate_required()


def test_config_success():
    _clear()
    os.environ.update(
        {
            "PROJECT_ID": "p",
            "REGION": "us",
            "DOC_AI_PROCESSOR_ID": "proc",
            "OPENAI_API_KEY": "k",
            "DRIVE_INPUT_FOLDER_ID": "in",
            "DRIVE_REPORT_FOLDER_ID": "out",
            "INTAKE_GCS_BUCKET": "intake",
            "OUTPUT_GCS_BUCKET": "output",
            "SUMMARY_BUCKET": "summary",
            "INTERNAL_EVENT_TOKEN": "token",
        }
    )
    cfg = AppConfig()
    cfg.validate_required()  # no exception
    assert cfg.project_id == "p"


def test_config_resolves_secret(monkeypatch):
    _clear()
    client = SimpleNamespace(
        access_secret_version=lambda name: SimpleNamespace(
            payload=SimpleNamespace(data=b"resolved")
        )
    )
    monkeypatch.setattr(
        secrets_mod,
        "secretmanager",
        SimpleNamespace(SecretManagerServiceClient=lambda: client),
    )

    os.environ.update(
        {
            "PROJECT_ID": "proj",
            "REGION": "us",
            "DOC_AI_PROCESSOR_ID": "sm://doc-proc",
            "OPENAI_API_KEY": "sm://openai",
            "DRIVE_INPUT_FOLDER_ID": "sm://drive-in",
            "DRIVE_REPORT_FOLDER_ID": "sm://drive-out",
            "INTAKE_GCS_BUCKET": "sm://intake",
            "OUTPUT_GCS_BUCKET": "sm://output",
            "SUMMARY_BUCKET": "sm://summary",
            "INTERNAL_EVENT_TOKEN": "sm://token",
        }
    )
    cfg = AppConfig()
    assert cfg.doc_ai_processor_id == "resolved"
    assert cfg.openai_api_key == "resolved"
    _clear()
