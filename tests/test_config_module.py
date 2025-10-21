from __future__ import annotations

import pytest

from src.config import AppConfig, get_config, parse_bool


def test_parse_bool_variants():
    assert parse_bool("TRUE")
    assert parse_bool(" yes ")
    assert not parse_bool("off")
    assert not parse_bool(None)


def test_app_config_properties_and_validation(monkeypatch):
    required_env = {
        "PROJECT_ID": "proj-env",
        "REGION": "us-central1",
        "DOC_AI_PROCESSOR_ID": "proc-123",
        "OPENAI_API_KEY": "sk-test",
        "DRIVE_INPUT_FOLDER_ID": "drive-in",
        "DRIVE_REPORT_FOLDER_ID": "drive-out",
        "DRIVE_IMPERSONATION_USER": "impersonation@example.com",
        "INTAKE_GCS_BUCKET": "bucket-intake",
        "OUTPUT_GCS_BUCKET": "bucket-output",
        "SUMMARY_BUCKET": "bucket-output",
        "CMEK_KEY_NAME": "projects/proj/locations/us/keyRings/ring/cryptoKeys/key",
    }
    for key, value in required_env.items():
        monkeypatch.setenv(key, value)

    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", '{"type":"service_account"}')
    monkeypatch.setenv("USE_STRUCTURED_SUMMARISER", "false")
    monkeypatch.setenv("RUN_PIPELINE_INLINE", "off")

    cfg = AppConfig()

    assert cfg.use_structured_summariser is False
    assert cfg.run_pipeline_inline is False
    cfg.validate_required()  # does not raise

    monkeypatch.delenv("PROJECT_ID", raising=False)
    cfg_missing = AppConfig(
        project_id="",
        region="us-central1",
        doc_ai_processor_id="proc-123",
        openai_api_key="sk-test",
        drive_input_folder_id="drive-in",
        drive_report_folder_id="drive-out",
        intake_gcs_bucket="bucket-intake",
        output_gcs_bucket="bucket-output",
        summary_bucket="bucket-output",
    )
    with pytest.raises(RuntimeError) as excinfo:
        cfg_missing.validate_required()
    assert "project_id" in str(excinfo.value)


def test_get_config_cache(monkeypatch):
    monkeypatch.setenv("PROJECT_ID", "cache-proj-1")
    monkeypatch.setenv("REGION", "us")
    monkeypatch.setenv("DOC_AI_PROCESSOR_ID", "proc")
    monkeypatch.setenv("OPENAI_API_KEY", "sk")
    monkeypatch.setenv("DRIVE_INPUT_FOLDER_ID", "drive-in")
    monkeypatch.setenv("DRIVE_REPORT_FOLDER_ID", "drive-out")
    monkeypatch.setenv("DRIVE_IMPERSONATION_USER", "impersonation@example.com")
    monkeypatch.setenv("INTAKE_GCS_BUCKET", "intake")
    monkeypatch.setenv("OUTPUT_GCS_BUCKET", "output")
    monkeypatch.setenv("SUMMARY_BUCKET", "output")
    monkeypatch.setenv("CMEK_KEY_NAME", "projects/test/locations/us/keyRings/test/cryptoKeys/test")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", '{"type":"service_account"}')

    get_config.cache_clear()
    first = get_config()
    assert first.project_id == "cache-proj-1"

    monkeypatch.setenv("PROJECT_ID", "cache-proj-2")
    second = get_config()
    # Cached instance should still reflect first value
    assert second.project_id == "cache-proj-1"

    get_config.cache_clear()
    refreshed = get_config()
    assert refreshed.project_id == "cache-proj-2"
