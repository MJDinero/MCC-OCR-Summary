import pytest
pytest.skip("Legacy config alias test removed after refactor", allow_module_level=True)

from src.config import AppConfig


def test_legacy_parser_id_alias_populates_processor_id(monkeypatch):
    monkeypatch.delenv('DOC_AI_OCR_PROCESSOR_ID', raising=False)
    monkeypatch.setenv('DOC_AI_OCR_PARSER_ID', 'legacy123')
    monkeypatch.setenv('DOC_AI_LOCATION', 'us')
    monkeypatch.setenv('PROJECT_ID', 'proj')
    cfg = AppConfig()
    assert cfg.doc_ai_ocr_processor_id == 'legacy123'
    # validation should still pass if OpenAI omitted in stub mode
    monkeypatch.setenv('STUB_MODE', 'true')
    cfg.validate_required()
