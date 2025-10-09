import os
import pytest

from src.config import AppConfig


REQUIRED_KEYS = [
    'PROJECT_ID','REGION','DOC_AI_PROCESSOR_ID','OPENAI_API_KEY','DRIVE_INPUT_FOLDER_ID','DRIVE_REPORT_FOLDER_ID'
]


def _clear():
    for k in REQUIRED_KEYS:
        os.environ.pop(k, None)


def test_config_missing_required():
    _clear()
    os.environ['PROJECT_ID'] = 'p'
    cfg = AppConfig()
    with pytest.raises(RuntimeError):
        cfg.validate_required()


def test_config_success():
    _clear()
    os.environ.update({
        'PROJECT_ID':'p','REGION':'us','DOC_AI_PROCESSOR_ID':'proc','OPENAI_API_KEY':'k',
        'DRIVE_INPUT_FOLDER_ID':'in','DRIVE_REPORT_FOLDER_ID':'out'
    })
    cfg = AppConfig()
    cfg.validate_required()  # no exception
    assert cfg.project_id == 'p'
