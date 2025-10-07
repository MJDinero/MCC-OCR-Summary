"""Centralized application configuration (Phase B: MCC Drive Intake).

All environment / secret driven settings are consolidated here to eliminate
scattered per-module BaseSettings classes. Modules should `from mcc.config import get_config`
and access a cached `AppConfig` instance.
"""
from __future__ import annotations
from functools import lru_cache
from typing import Optional, List
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class AppConfig(BaseSettings):
    """Application configuration loaded from environment.

    All variables validate on instantiation; call `validate_required()` in startup
    to enforce presence of mandatory runtime secrets in non-stub mode.
    """

    # Core / project
    project_id: str = Field('', validation_alias='PROJECT_ID')
    gcp_project_id: Optional[str] = Field(None, validation_alias='GCP_PROJECT_ID')
    region: str | None = Field(None, validation_alias='REGION')

    # External resource identifiers / secrets
    openai_api_key: str | None = Field(None, validation_alias='OPENAI_API_KEY')
    drive_root_folder_id: str | None = Field(None, validation_alias='DRIVE_ROOT_FOLDER_ID')
    drive_intake_folder_id: str | None = Field(None, validation_alias='DRIVE_INTAKE_FOLDER_ID')
    sheet_id: str | None = Field(None, validation_alias='SHEET_ID')
    artifact_bucket: str | None = Field(None, validation_alias='ARTIFACT_BUCKET')
    claim_log_salt: str | None = Field(None, validation_alias='CLAIM_LOG_SALT')
    allowed_origins: str | None = Field(None, validation_alias='ALLOWED_ORIGINS')  # comma separated

    # Feature / processing flags
    stub_mode: bool = Field(False, validation_alias='STUB_MODE')
    full_processing: bool = Field(True, validation_alias='FULL_PROCESSING')
    drive_enabled: bool = Field(False, validation_alias='DRIVE_ENABLED')
    write_to_drive: bool = Field(False, validation_alias='WRITE_TO_DRIVE')
    sheets_enabled: bool = Field(False, validation_alias='SHEETS_ENABLED')
    pdf_enabled: bool = Field(False, validation_alias='PDF_ENABLED')
    pdf_template: str = Field('executive_mcc_v3.html', validation_alias='PDF_TEMPLATE')
    enable_metrics: bool = Field(True, validation_alias='ENABLE_METRICS')
    max_pdf_bytes: int = Field(80 * 1024 * 1024, validation_alias='MAX_PDF_BYTES')  # 80MB default

    # DocAI processors
    doc_ai_form_parser_id: str = Field('', validation_alias='DOC_AI_FORM_PARSER_ID')
    doc_ai_ocr_parser_id: str = Field('', validation_alias='DOC_AI_OCR_PROCESSOR_ID')
    doc_ai_invoice_processor: str | None = Field(None, validation_alias='DOC_AI_INVOICE_PROCESSOR')
    doc_ai_splitter_processor: str | None = Field(None, validation_alias='DOC_AI_SPLITTER_PROCESSOR')
    doc_ai_classifier_processor: str | None = Field(None, validation_alias='DOC_AI_CLASSIFIER_PROCESSOR')
    doc_ai_location: str = Field('us', validation_alias='DOC_AI_LOCATION')

    # Sheets / Drive specifics
    sheet_tab_gid: Optional[int] = Field(None, validation_alias='SHEET_TAB_GID')

    model_config = SettingsConfigDict(env_file='.env', extra='ignore', case_sensitive=False)

    @property
    def effective_project(self) -> str:
        return self.project_id or (self.gcp_project_id or '')

    @property
    def cors_origins(self) -> list[str]:
        if not self.allowed_origins:
            return ["*"]
        return [o.strip() for o in self.allowed_origins.split(',') if o.strip()]

    @model_validator(mode='after')
    def _normalize(self):  # type: ignore[override]
        # Ensure location formatting
        if self.doc_ai_location:
            self.doc_ai_location = self.doc_ai_location.strip()
        return self

    def _fallback_secret(self, name: str) -> Optional[str]:  # pragma: no cover - network / GCP optional
        """Attempt to fetch a secret from Secret Manager if available.

        Format: projects/{project}/secrets/{name}/versions/latest
        Only used if env var missing and stub_mode is False.
        """
        try:
            from google.cloud import secretmanager  # type: ignore
        except Exception:
            return None
        if not self.effective_project:
            return None
        try:
            client = secretmanager.SecretManagerServiceClient()
            secret_path = f"projects/{self.effective_project}/secrets/{name}/versions/latest"
            response = client.access_secret_version(name=secret_path)
            return response.payload.data.decode('utf-8')  # type: ignore[attr-defined]
        except Exception:
            return None

    def validate_required(self) -> None:
        """Fail fast if required runtime settings are missing.

        Skips some checks when stub_mode is enabled to facilitate local dev.
        """
        missing: List[str] = []
        required = [
            ('project_id', self.effective_project),
            ('doc_ai_location', self.doc_ai_location),
            ('doc_ai_ocr_parser_id', self.doc_ai_ocr_parser_id),
        ]
        if not self.stub_mode:
            if not self.openai_api_key:
                # Try secret fallback
                fetched = self._fallback_secret('OPENAI_API_KEY')
                if fetched:
                    object.__setattr__(self, 'openai_api_key', fetched)
            required.append(('openai_api_key', self.openai_api_key))
        for key, value in required:
            if not value:
                missing.append(key)
        if missing:
            raise RuntimeError(f"Missing required configuration values: {', '.join(missing)}")

@lru_cache
def get_config() -> AppConfig:
    return AppConfig()

__all__ = ['AppConfig', 'get_config']
