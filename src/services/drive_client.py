"""Google Drive helper functions (minimal, idempotent).

Provides two primary functions used by the pipeline:
 - download_pdf(file_id) -> bytes
 - upload_pdf(file_bytes, report_name) -> uploaded file id

Authentication relies on GOOGLE_APPLICATION_CREDENTIALS env or default creds.
"""
# pylint: disable=no-member
from __future__ import annotations

import io
import logging
import time
import json
import re
import os
from typing import Any, Dict, Optional, Tuple
from googleapiclient.discovery import build  # type: ignore
from googleapiclient.errors import HttpError  # type: ignore
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload  # type: ignore
from google.oauth2 import service_account  # type: ignore

from src.config import get_config
from src.errors import DriveServiceError
from src.utils.logging_utils import structured_log

_SCOPES = ["https://www.googleapis.com/auth/drive"]
_LOG = logging.getLogger("drive_client")
_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{10,}")

def _merge_context(base: Dict[str, Any] | None, defaults: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {**defaults}
    if base:
        merged.update(base)
    merged.setdefault("trace_id", None)
    merged.setdefault("phase", defaults.get("phase"))
    return merged


def _drive_service(log_context: Optional[Dict[str, Any]] = None):
    impersonate_user = os.getenv("DRIVE_IMPERSONATION_USER")
    raw_credentials = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not raw_credentials:
        try:
            raw_credentials = getattr(get_config(), "google_application_credentials", None)
        except Exception:  # pragma: no cover - config loading failures raised later
            raw_credentials = None

    if not raw_credentials:
        raise DriveServiceError("GOOGLE_APPLICATION_CREDENTIALS is not configured")

    raw_credentials = str(raw_credentials).strip()
    subject = impersonate_user if impersonate_user else None
    creds = None
    if raw_credentials.startswith("{"):
        try:
            service_account_info = json.loads(raw_credentials)
        except json.JSONDecodeError as exc:  # pragma: no cover - configuration error
            raise DriveServiceError("GOOGLE_APPLICATION_CREDENTIALS contains invalid JSON") from exc
        creds = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=_SCOPES,
            subject=subject,
        )  # type: ignore[arg-type]
    elif os.path.exists(raw_credentials):
        try:
            creds = service_account.Credentials.from_service_account_file(
                raw_credentials,
                scopes=_SCOPES,
                subject=subject,
            )  # type: ignore[arg-type]
        except Exception as exc:  # pragma: no cover - unexpected credential parse failure
            context = _merge_context(log_context, {"phase": "drive_credentials"})
            structured_log(_LOG, logging.ERROR, "drive_credentials_invalid", error=str(exc), **context)
            raise DriveServiceError(f"Failed to parse GOOGLE_APPLICATION_CREDENTIALS: {exc}") from exc
    else:
        raise DriveServiceError(f"Missing GOOGLE_APPLICATION_CREDENTIALS file at {raw_credentials!r}")

    try:
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception as exc:  # pragma: no cover - discovery failures
        context = _merge_context(log_context, {"phase": "drive_service_create"})
        structured_log(_LOG, logging.ERROR, "drive_service_initialisation_failed", error=str(exc), **context)
        raise DriveServiceError(f"Failed to initialise Drive service: {exc}") from exc
    try:
        about_info = (
            service.about()
            .get(fields="user")
            .execute()
        )
        context = _merge_context(log_context, {"phase": "drive_service_create"})
        structured_log(
            _LOG,
            logging.INFO,
            "drive_impersonation_check",
            impersonation_user=impersonate_user,
            resolved_user=about_info.get("user", {}).get("emailAddress"),
            **context,
        )
    except Exception as exc:  # pragma: no cover - diagnostics only
        context = _merge_context(log_context, {"phase": "drive_service_create"})
        structured_log(_LOG, logging.WARNING, "drive_about_check_failed", error=str(exc), **context)
    return service


def _resolve_folder_metadata(folder_id: str, *, log_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Retrieve Drive metadata for the target folder (cached per process)."""
    context = _merge_context(log_context, {"phase": "drive_folder_lookup", "folder_id": folder_id})
    service = _drive_service(context)
    try:  # pragma: no cover - external API call
        request = service.files().get(
            fileId=folder_id,
            fields='id,name,driveId,parents,capabilities(canAddChildren),permissionIds',
            supportsAllDrives=True,
            supportsTeamDrives=True,
        )
    except TypeError:  # older stubs/tests
        request = service.files().get(
            fileId=folder_id,
            fields='id,name,driveId,parents,capabilities(canAddChildren),permissionIds',
        )
    metadata = request.execute()
    structured_log(
        _LOG,
        logging.INFO,
        "drive_folder_metadata",
        **_merge_context(
            log_context,
            {
                "folder_id": folder_id,
                "phase": "drive_folder_lookup",
                "drive_id": metadata.get("driveId"),
                "can_add_children": metadata.get("capabilities", {}).get("canAddChildren"),
            },
        ),
    )
    return metadata


def _extract_ids(raw_folder: str, raw_drive: str | None) -> Tuple[str, Optional[str]]:
    """Normalise folder/drive identifiers from env/secret inputs."""

    folder_candidate = (raw_folder or "").strip()
    drive_candidate = (raw_drive or "").strip() or None

    json_candidate: dict[str, Any] | None = None
    if folder_candidate.startswith("{") and folder_candidate.endswith("}"):
        try:
            parsed = json.loads(folder_candidate)
            if isinstance(parsed, dict):
                json_candidate = parsed
        except json.JSONDecodeError:
            json_candidate = None
    if json_candidate:
        for key in ("folderId", "folder_id", "id", "folder", "reportFolderId"):
            value = json_candidate.get(key)
            if isinstance(value, str) and value.strip():
                folder_candidate = value.strip()
                break
        for key in ("driveId", "drive_id", "sharedDriveId", "shared_drive_id", "teamDriveId", "team_drive_id"):
            value = json_candidate.get(key)
            if isinstance(value, str) and value.strip():
                drive_candidate = value.strip()
                break

    if ":" in folder_candidate and " " not in folder_candidate:
        maybe_drive, maybe_folder = folder_candidate.split(":", 1)
        if maybe_drive and maybe_drive.startswith("0A") and len(maybe_folder) >= 10:
            drive_candidate = drive_candidate or maybe_drive.strip()
            folder_candidate = maybe_folder

    matches = _ID_PATTERN.findall(folder_candidate)
    folder_id: Optional[str] = None
    drive_from_matches: Optional[str] = None
    for candidate in matches:
        if candidate.startswith("0A"):
            if drive_from_matches is None:
                drive_from_matches = candidate
            if folder_id is None and folder_candidate.strip().startswith("0A"):
                folder_id = candidate
        elif folder_id is None:
            folder_id = candidate
    if folder_id is None and matches:
        folder_id = matches[-1]
    if folder_id is None and drive_from_matches:
        folder_id = drive_from_matches

    if not folder_id:
        folder_id = folder_candidate.strip().strip("\"' ")
    else:
        folder_id = folder_id.strip()

    if not folder_id:
        raise RuntimeError("drive_report_folder_id is not set or invalid")

    drive_candidate = drive_candidate or drive_from_matches
    if drive_candidate:
        drive_match = _ID_PATTERN.findall(drive_candidate)
        drive_candidate = drive_match[0] if drive_match else drive_candidate.strip().strip("\"' ")
        if drive_candidate and not drive_candidate.startswith("0A"):
            # Invalid shared drive IDs start with 0A; discard obvious mismatch.
            drive_candidate = None

    return folder_id, drive_candidate


def download_pdf(file_id: str, *, log_context: Optional[Dict[str, Any]] = None) -> bytes:
    if not file_id:
        raise DriveServiceError('file_id required')
    context = _merge_context(log_context, {"phase": "drive_download", "file_id": file_id})
    structured_log(_LOG, logging.INFO, "drive_download_start", **context)
    service = _drive_service(context)
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
        context["error"] = "not_pdf"
        structured_log(_LOG, logging.ERROR, "drive_download_failure", **context)
        raise DriveServiceError('Downloaded file is not a PDF')
    structured_log(_LOG, logging.INFO, "drive_download_success", bytes=len(data), **context)
    return data


def upload_pdf(
    file_bytes: bytes,
    report_name: str,
    *,
    parent_folder_id: str | None = None,
    log_context: Optional[Dict[str, Any]] = None,
) -> str:
    if not file_bytes or not file_bytes.startswith(b'%PDF-'):
        raise DriveServiceError('file_bytes must be a PDF (bytes starting with %PDF-)')
    cfg = get_config()
    folder_source = parent_folder_id or cfg.drive_report_folder_id
    shared_drive_source = (
        getattr(cfg, "drive_shared_drive_id", None) or os.getenv("DRIVE_SHARED_DRIVE_ID", None)
    )
    folder_id, drive_id = _extract_ids(folder_source, shared_drive_source)
    context = _merge_context(
        log_context,
        {
            "phase": "drive_upload",
            "folder_id": folder_id,
            "drive_id": drive_id,
            "report_name": report_name,
        },
    )
    try:
        folder_meta = _resolve_folder_metadata(folder_id, log_context=context)
    except HttpError as err:
        structured_log(
            _LOG,
            logging.ERROR,
            "drive_folder_lookup_failed",
            error=str(err),
            status_code=getattr(err, "status_code", None),
            **context,
        )
        raise DriveServiceError(f"Failed to fetch Drive folder metadata: {err}") from err

    capabilities = folder_meta.get("capabilities") or {}
    can_add_children = bool(capabilities.get("canAddChildren"))
    context["can_add_children"] = can_add_children

    derived_drive_id = folder_meta.get("driveId")
    if derived_drive_id and derived_drive_id.startswith("0A"):
        drive_id = derived_drive_id
    elif not derived_drive_id and not drive_id:
        structured_log(
            _LOG,
            logging.WARNING,
            "drive_folder_not_shared",
            message="Folder metadata missing driveId; likely My Drive",
            **context,
        )
    context["drive_id"] = drive_id or derived_drive_id
    service = _drive_service(context)
    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype='application/pdf', resumable=True)
    file_metadata: dict[str, Any] = {
        'name': report_name,
        'mimeType': 'application/pdf',
        'parents': [folder_id],
    }
    structured_log(
        _LOG,
        logging.INFO,
        "drive_upload_start",
        bytes=len(file_bytes),
        **context,
    )
    started_at = time.perf_counter()
    try:  # pragma: no cover
        request = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id,name,parents,driveId,webViewLink',
            supportsAllDrives=True,
            supportsTeamDrives=True,
            enforceSingleParent=True,
        )
    except TypeError:
        request = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id,name,parents,driveId,webViewLink',
            enforceSingleParent=True,
        )

    try:  # pragma: no cover
        created = request.execute()
    except HttpError as err:
        error_reason = None
        error_message = None
        try:
            payload = json.loads(err.content.decode("utf-8"))
            error_info = payload.get("error", {})
            if isinstance(error_info, dict):
                error_message = error_info.get("message")
                errors = error_info.get("errors")
                if isinstance(errors, list) and errors:
                    error_reason = errors[0].get("reason")
        except Exception:  # noqa: BLE001
            error_reason = None
        structured_log(
            _LOG,
            logging.ERROR,
            "drive_upload_failure",
            error=str(err),
            error_reason=error_reason,
            error_message=error_message,
            **context,
        )
        raise DriveServiceError(f"Drive upload failed: {error_message or err}") from err

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
            "drive_id": created.get('driveId') or drive_id,
            "parent": folder_id,
        }
    )
    structured_log(_LOG, logging.INFO, "drive_upload_success", **context)
    return created['id']

__all__ = ['download_pdf', 'upload_pdf']
