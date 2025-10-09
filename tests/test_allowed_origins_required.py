import pytest
pytest.skip("Legacy test removed after refactor: allowed origins no longer enforced", allow_module_level=True)

"""(File retained only to avoid git noise; logic removed)"""

import os
import pytest

from src.config import AppConfig


def _base_env():
    os.environ['PROJECT_ID'] = 'proj'
    os.environ['DOC_AI_OCR_PROCESSOR_ID'] = 'proc'
    os.environ['DOC_AI_LOCATION'] = 'us'
    os.environ.pop('STUB_MODE', None)
    os.environ['OPENAI_API_KEY'] = 'dummy'


def test_allowed_origins_missing_in_non_stub_mode():
    _base_env()
    os.environ.pop('ALLOWED_ORIGINS', None)
    cfg = AppConfig()
    with pytest.raises(RuntimeError) as exc:
        cfg.validate_required()
    assert 'allowed_origins' in str(exc.value)


def test_allowed_origins_wildcard_rejected_in_non_stub_mode():
    _base_env()
    os.environ['ALLOWED_ORIGINS'] = '*'
    cfg = AppConfig()
    with pytest.raises(RuntimeError) as exc:
        cfg.validate_required()
    assert 'allowed_origins' in str(exc.value)


def test_allowed_origins_ok_when_specified():
    _base_env()
    os.environ['ALLOWED_ORIGINS'] = 'https://a.com,https://b.com'
    cfg = AppConfig()
    # Should not raise
    cfg.validate_required()
    assert cfg.cors_origins == ['https://a.com', 'https://b.com']
