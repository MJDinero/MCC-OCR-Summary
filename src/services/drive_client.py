"""Google Drive helper functions (minimal, idempotent).

Provides two primary functions used by the pipeline:
 - download_pdf(file_id) -> bytes
 - upload_pdf(file_bytes, report_name) -> uploaded file id

Authentication relies on GOOGLE_APPLICATION_CREDENTIALS env or default creds.
"""
from __future__ import annotations

import io
import logging
from typing import Any
from googleapiclient.discovery import build  # type: ignore
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload  # type: ignore
from google.oauth2 import service_account  # type: ignore
import os

from src.config import get_config

_SCOPES = ["https://www.googleapis.com/auth/drive"]


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
    service = _drive_service()
    # Include supportsAllDrives/includeItemsFromAllDrives for Shared Drive compatibility
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)  # type: ignore[attr-defined]
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _status, done = downloader.next_chunk()  # _status unused; progress not logged for minimal impl
    data = buf.getvalue()
    if not data.startswith(b'%PDF-'):
        raise ValueError('Downloaded file is not a PDF')
    return data


def upload_pdf(file_bytes: bytes, report_name: str) -> str:
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
    logging.info("Starting Drive upload (Shared Drive mode)...")
    created = service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id',
        supportsAllDrives=True,
    ).execute()  # type: ignore[attr-defined]
    logging.info("Drive upload complete → ID: %s", created.get('id'))
    return created['id']

__all__ = ['download_pdf', 'upload_pdf']
