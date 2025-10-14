"""Google Drive helper functions (minimal, idempotent).

Provides two primary functions used by the pipeline:
 - download_pdf(file_id) -> bytes
 - upload_pdf(file_bytes, report_name) -> uploaded file id

Authentication relies on GOOGLE_APPLICATION_CREDENTIALS env or default creds.
"""
from __future__ import annotations

import io
import logging
import time
from typing import Any, Dict, Optional
from googleapiclient.discovery import build  # type: ignore
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload  # type: ignore
from google.oauth2 import service_account  # type: ignore
import os

from src.config import get_config

_SCOPES = ["https://www.googleapis.com/auth/drive"]
_LOG = logging.getLogger("drive_client")


def _drive_service():
    creds = None
    gac_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
    if gac_path and os.path.exists(gac_path):
        creds = service_account.Credentials.from_service_account_file(gac_path, scopes=_SCOPES)  # type: ignore[arg-type]
    # Fallback: Application Default Credentials (cloud runtime) automatically picked up if creds is None
    return build('drive', 'v3', credentials=creds, cache_discovery=False)


def download_pdf(file_id: str) -> bytes:
    if not file_id:
        raise ValueError('file_id required')
    _LOG.info("drive_download_started", extra={"file_id": file_id})
    service = _drive_service()
    # Attempt Shared Drive parameter if supported (mock in tests doesn't accept it)
    try:  # pragma: no cover - thin wrapper
        request = service.files().get_media(fileId=file_id, supportsAllDrives=True)  # type: ignore[attr-defined]
    except TypeError:  # fallback for environments / mocks without param
        request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _status, done = downloader.next_chunk()  # _status unused; progress not logged for minimal impl
    data = buf.getvalue()
    if not data.startswith(b'%PDF-'):
        raise ValueError('Downloaded file is not a PDF')
    _LOG.info("drive_download_complete", extra={"file_id": file_id, "bytes": len(data)})
    return data


def upload_pdf(file_bytes: bytes, report_name: str, *, log_context: Optional[Dict[str, Any]] = None) -> str:
    if not file_bytes or not file_bytes.startswith(b'%PDF-'):
        raise ValueError('file_bytes must be a PDF (bytes starting with %PDF-)')
    cfg = get_config()
    folder_id = cfg.drive_report_folder_id
    if not folder_id:
        raise RuntimeError('drive_report_folder_id not configured')
    service = _drive_service()
    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype='application/pdf', resumable=False)
    file_metadata: dict[str, Any] = {
        'name': report_name,
        'mimeType': 'application/pdf',
        'parents': [folder_id],
    }
    _LOG.info(
        "drive_upload_started",
        extra={"report_name": report_name, "bytes": len(file_bytes), "parents": [folder_id]},
    )
    started_at = time.perf_counter()
    try:  # pragma: no cover
        created = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id',
            supportsAllDrives=True,
        ).execute()  # type: ignore[attr-defined]
    except TypeError:  # mocks / older client
        created = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id',
        ).execute()
    duration_ms = int((time.perf_counter() - started_at) * 1000)
    context = dict(log_context or {})
    context.setdefault("job_id", None)
    context.setdefault("trace_id", None)
    context.setdefault("document_id", report_name)
    context.setdefault("shard_id", "aggregate")
    context.setdefault("schema_version", cfg.summary_schema_version or os.getenv("SUMMARY_SCHEMA_VERSION", "2025-10-01"))
    context.setdefault("attempt", 1)
    context.setdefault("component", "drive_client")
    context.setdefault("severity", "INFO")
    if context.get("trace_id") and "logging.googleapis.com/trace" not in context:
        context["logging.googleapis.com/trace"] = f"projects/{cfg.project_id}/traces/{context['trace_id']}"
    context.update(
        {
            "duration_ms": duration_ms,
            "drive_file_id": created.get('id'),
            "bytes": len(file_bytes),
        }
    )
    _LOG.info("drive_upload_complete", extra=context)
    return created['id']

__all__ = ['download_pdf', 'upload_pdf']
