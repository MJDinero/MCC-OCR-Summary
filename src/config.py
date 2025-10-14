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


def parse_bool(value: str | None) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class AppConfig(BaseSettings):
    project_id: str = Field('', validation_alias='PROJECT_ID')
    # Accept legacy DOC_AI_LOCATION as alias for REGION (prefer REGION if both present)
    region: str = Field('us', validation_alias=AliasChoices('REGION', 'DOC_AI_LOCATION'))
    doc_ai_processor_id: str = Field('', validation_alias='DOC_AI_PROCESSOR_ID')
    doc_ai_splitter_id: str | None = Field(None, validation_alias='DOC_AI_SPLITTER_PROCESSOR_ID')
    openai_api_key: str | None = Field(None, validation_alias='OPENAI_API_KEY')
    openai_model: str | None = Field(None, validation_alias='OPENAI_MODEL')
    # Feature flag (defaults enabled) to force StructuredSummariser usage.
    # Raw env capture (still allow pydantic to populate) then we post-process to strict bool via parse_bool
    use_structured_summariser_raw: str | bool | None = Field(True, validation_alias='USE_STRUCTURED_SUMMARISER')
    drive_input_folder_id: str = Field('', validation_alias='DRIVE_INPUT_FOLDER_ID')
    drive_report_folder_id: str = Field('', validation_alias='DRIVE_REPORT_FOLDER_ID')
    intake_gcs_bucket: str = Field('quantify-agent-intake', validation_alias='INTAKE_GCS_BUCKET')
    output_gcs_bucket: str = Field('quantify-agent-output', validation_alias='OUTPUT_GCS_BUCKET')
    summary_bucket: str = Field('quantify-agent-output', validation_alias='SUMMARY_BUCKET')
    pipeline_pubsub_topic: str | None = Field(None, validation_alias='PIPELINE_PUBSUB_TOPIC')
    run_pipeline_inline_raw: str | bool | None = Field(True, validation_alias='RUN_PIPELINE_INLINE')
    pipeline_workflow_name: str | None = Field(None, validation_alias='PIPELINE_WORKFLOW_NAME')
    summary_schema_version: str = Field('2025-10-01', validation_alias='SUMMARY_SCHEMA_VERSION')
    max_shard_concurrency: int = Field(12, validation_alias='MAX_SHARD_CONCURRENCY')
    # Google credentials path is read by google-auth automatically; keep for documentation completeness
    google_application_credentials: str | None = Field(None, validation_alias='GOOGLE_APPLICATION_CREDENTIALS')

    # Hard (safe) defaults
    max_pdf_bytes: int = 20 * 1024 * 1024  # 20MB limit for uploaded PDFs
    model_config = SettingsConfigDict(env_file='.env', extra='ignore', case_sensitive=False)

    @property
    def use_structured_summariser(self) -> bool:  # accessor applying robust parsing
        raw = self.use_structured_summariser_raw
        if isinstance(raw, bool):
            return raw
        return parse_bool(str(raw))

    @property
    def run_pipeline_inline(self) -> bool:
        raw = self.run_pipeline_inline_raw
        if isinstance(raw, bool):
            return raw
        return parse_bool(str(raw))

    def validate_required(self) -> None:
        # Primary value-based validation (empty / falsy fields)
        required_pairs = [
            ("project_id", self.project_id, "PROJECT_ID"),
            ("region", self.region, "REGION"),
            ("doc_ai_processor_id", self.doc_ai_processor_id, "DOC_AI_PROCESSOR_ID"),
            ("openai_api_key", self.openai_api_key, "OPENAI_API_KEY"),
            ("drive_input_folder_id", self.drive_input_folder_id, "DRIVE_INPUT_FOLDER_ID"),
            ("drive_report_folder_id", self.drive_report_folder_id, "DRIVE_REPORT_FOLDER_ID"),
            ("intake_gcs_bucket", self.intake_gcs_bucket, "INTAKE_GCS_BUCKET"),
            ("output_gcs_bucket", self.output_gcs_bucket, "OUTPUT_GCS_BUCKET"),
            ("summary_bucket", self.summary_bucket, "SUMMARY_BUCKET"),
        ]
        missing = [name for name, value, _env in required_pairs if not value]

        # Secondary safeguard for test environments: if a field has a value but its
        # corresponding env var is absent entirely, treat it as missing so that tests
        # which purposely clear environment variables still detect the absence.
        # This covers scenarios where upstream tooling injects fallback defaults.
        import os as _os  # local import to avoid polluting module namespace
        # NOTE (v11j-fix): REGION has a safe default ('us') so we no longer require the
        # explicit env var to be present when a non-empty value already exists. This
        # prevents startup failures in environments where REGION was omitted but a
        # sensible default is acceptable. All other variables remain strictly required.
        strict_env_presence = {
            "PROJECT_ID",
            "DOC_AI_PROCESSOR_ID",
            "OPENAI_API_KEY",
            "DRIVE_INPUT_FOLDER_ID",
            "DRIVE_REPORT_FOLDER_ID",
            "INTAKE_GCS_BUCKET",
            "OUTPUT_GCS_BUCKET",
            "SUMMARY_BUCKET",
        }
        unmet_env: list[str] = []
        for name, value, env_name in required_pairs:
            if env_name not in _os.environ and env_name in strict_env_presence and name not in missing:
                if value not in (None, ""):
                    # Even if value is non-empty but came from a default and env is absent
                    # we enforce explicit provisioning for strict vars.
                    missing.append(name)
                else:
                    unmet_env.append(env_name)
        if missing:
            raise RuntimeError("Missing required configuration values: " + ", ".join(sorted(set(missing))))
        if unmet_env:
            raise RuntimeError("Missing environment variables: " + ", ".join(sorted(set(unmet_env))))


@lru_cache
def get_config() -> AppConfig:
    return AppConfig()  # type: ignore[call-arg]


__all__ = ["AppConfig", "get_config"]
