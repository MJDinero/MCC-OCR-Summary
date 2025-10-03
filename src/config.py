"""Centralized application configuration (Phase B: MCC Drive Intake).

All environment / secret driven settings are consolidated here to eliminate
scattered per-module BaseSettings classes. Modules should `from mcc.config import get_config`
and access a cached `AppConfig` instance.
"""
from __future__ import annotations
from functools import lru_cache
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class AppConfig(BaseSettings):
    # Core / project
    project_id: str = Field('', validation_alias='PROJECT_ID')
    gcp_project_id: Optional[str] = Field(None, validation_alias='GCP_PROJECT_ID')
    # Processing flags
    stub_mode: bool = Field(False, validation_alias='STUB_MODE')
    full_processing: bool = Field(True, validation_alias='FULL_PROCESSING')
    drive_enabled: bool = Field(False, validation_alias='DRIVE_ENABLED')
    write_to_drive: bool = Field(False, validation_alias='WRITE_TO_DRIVE')
    sheets_enabled: bool = Field(False, validation_alias='SHEETS_ENABLED')
    pdf_enabled: bool = Field(False, validation_alias='PDF_ENABLED')
    pdf_template: str = Field('executive_mcc_v3.html', validation_alias='PDF_TEMPLATE')

    # DocAI
    doc_ai_form_parser_id: str = Field('', validation_alias='DOC_AI_FORM_PARSER_ID')
    # Primary OCR parser (renamed from legacy 'layout')
    doc_ai_ocr_parser_id: str = Field('', validation_alias='DOC_AI_OCR_PROCESSOR_ID')
    doc_ai_location: str = Field('us', validation_alias='DOC_AI_LOCATION')

    # Sheets / Drive specifics
    sheet_tab_gid: Optional[int] = Field(None, validation_alias='SHEET_TAB_GID')

    model_config = SettingsConfigDict(env_file='.env', extra='ignore', case_sensitive=False)

    @property
    def effective_project(self) -> str:
        return self.project_id or (self.gcp_project_id or '')

@lru_cache
def get_config() -> AppConfig:
    return AppConfig()

__all__ = ['AppConfig', 'get_config']
