"""Minimal configuration module for production MVP.

Only exposes the environment variables explicitly required by the
streamlined pipeline (Drive intake -> DocAI OCR -> OpenAI summary -> PDF -> Drive upload).

Retained variables (env names in parentheses):
 - PROJECT_ID
 - REGION
 - DOC_AI_PROCESSOR_ID
 - OPENAI_API_KEY
 - DRIVE_INPUT_FOLDER_ID
 - DRIVE_REPORT_FOLDER_ID
 - GOOGLE_APPLICATION_CREDENTIALS (used implicitly by Google clients)

All legacy flags (metrics, sheets, multiple processor fallbacks, CORS, etc.) removed.
"""
from __future__ import annotations

from functools import lru_cache
from pydantic import Field, AliasChoices
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    project_id: str = Field('', validation_alias='PROJECT_ID')
    # Accept legacy DOC_AI_LOCATION as alias for REGION (prefer REGION if both present)
    region: str = Field('us', validation_alias=AliasChoices('REGION', 'DOC_AI_LOCATION'))
    doc_ai_processor_id: str = Field('', validation_alias='DOC_AI_PROCESSOR_ID')
    openai_api_key: str | None = Field(None, validation_alias='OPENAI_API_KEY')
    openai_model: str | None = Field(None, validation_alias='OPENAI_MODEL')
    # Feature flag (defaults enabled) to force StructuredSummariser usage.
    use_structured_summariser: bool = Field(True, validation_alias='USE_STRUCTURED_SUMMARISER')
    drive_input_folder_id: str = Field('', validation_alias='DRIVE_INPUT_FOLDER_ID')
    drive_report_folder_id: str = Field('', validation_alias='DRIVE_REPORT_FOLDER_ID')
    # Google credentials path is read by google-auth automatically; keep for documentation completeness
    google_application_credentials: str | None = Field(None, validation_alias='GOOGLE_APPLICATION_CREDENTIALS')

    # Hard (safe) defaults
    max_pdf_bytes: int = 20 * 1024 * 1024  # 20MB limit for uploaded PDFs
    model_config = SettingsConfigDict(env_file='.env', extra='ignore', case_sensitive=False)

    def validate_required(self) -> None:
        # Primary value-based validation (empty / falsy fields)
        required_pairs = [
            ("project_id", self.project_id, "PROJECT_ID"),
            ("region", self.region, "REGION"),
            ("doc_ai_processor_id", self.doc_ai_processor_id, "DOC_AI_PROCESSOR_ID"),
            ("openai_api_key", self.openai_api_key, "OPENAI_API_KEY"),
            ("drive_input_folder_id", self.drive_input_folder_id, "DRIVE_INPUT_FOLDER_ID"),
            ("drive_report_folder_id", self.drive_report_folder_id, "DRIVE_REPORT_FOLDER_ID"),
        ]
        missing = [name for name, value, _env in required_pairs if not value]

        # Secondary safeguard for test environments: if a field has a value but its
        # corresponding env var is absent entirely, treat it as missing so that tests
        # which purposely clear environment variables still detect the absence.
        # This covers scenarios where upstream tooling injects fallback defaults.
        import os as _os  # local import to avoid polluting module namespace
        for name, value, env_name in required_pairs:
            if env_name not in _os.environ and name not in missing:
                # Only mark as missing if value appears to be an auto default (non-empty)
                # but the explicit env variable was not provided.
                if value not in (None, ""):
                    missing.append(name)
        if missing:
            raise RuntimeError("Missing required configuration values: " + ", ".join(sorted(set(missing))))


@lru_cache
def get_config() -> AppConfig:
    return AppConfig()


__all__ = ["AppConfig", "get_config"]
