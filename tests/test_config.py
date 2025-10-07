import os
import pytest

from src.config import AppConfig


def test_config_missing_required_raises():
    os.environ.pop('PROJECT_ID', None)
    os.environ['DOC_AI_OCR_PROCESSOR_ID'] = 'pid'
    os.environ['DOC_AI_LOCATION'] = 'us'
    cfg = AppConfig()
    with pytest.raises(RuntimeError):
        cfg.validate_required()


def test_config_success_stub_mode():
    os.environ['PROJECT_ID'] = 'proj'
    os.environ['DOC_AI_OCR_PROCESSOR_ID'] = 'pid'
    os.environ['DOC_AI_LOCATION'] = 'us'
    os.environ['STUB_MODE'] = 'true'
    # No OpenAI key required in stub mode
    cfg = AppConfig()
    cfg.validate_required()  # should not raise
    assert 'proj' == cfg.effective_project


def test_cors_origins_parsing():
    os.environ['ALLOWED_ORIGINS'] = 'https://a.com, https://b.com'
    cfg = AppConfig()
    origins = cfg.cors_origins
    assert origins == ['https://a.com', 'https://b.com']