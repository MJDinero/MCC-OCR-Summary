"""Process routes for MCC OCR Summary FastAPI service."""

from __future__ import annotations

import logging
import os
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence, Tuple

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse, Response

from src.errors import (
    DriveServiceError,
    OCRServiceError,
    PDFGenerationError,
    PdfValidationError,
    SummarizationError,
    ValidationError,
)
from src.services.process_pipeline import ProcessPipelineResult
from src.services import drive_client as drive_client_module
from src.services.bible import (
    CANONICAL_ENTITY_ORDER,
    CANONICAL_NARRATIVE_ORDER,
    CANONICAL_SECTION_CONFIG,
    FORBIDDEN_PDF_PHRASES,
)
from src.utils.logging_utils import structured_log

router = APIRouter()

_API_LOG = logging.getLogger("api")
_SECTION_DEFINITIONS: Tuple[Tuple[str, str, bool, str], ...] = tuple(
    (
        heading,
        str(CANONICAL_SECTION_CONFIG[heading]["key"]),
        bool(CANONICAL_SECTION_CONFIG[heading]["bullet"]),
        str(CANONICAL_SECTION_CONFIG[heading]["fallback"]),
    )
    for heading in CANONICAL_NARRATIVE_ORDER
)

_LIST_DEFINITIONS: Tuple[Tuple[str, str, str], ...] = tuple(
    (
        heading,
        str(CANONICAL_SECTION_CONFIG[heading]["key"]),
        str(CANONICAL_SECTION_CONFIG[heading]["fallback"]),
    )
    for heading in CANONICAL_ENTITY_ORDER
)


def _mask_drive_id(file_id: str | None) -> str | None:
    if not file_id:
        return None
    token = file_id.strip()
    if len(token) <= 8:
        return token[:2] + "***"
    return f"{token[:4]}***{token[-4:]}"


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _drive_poll_batch_limit() -> int:
    raw = os.getenv("DRIVE_POLL_BATCH_LIMIT")
    try:
        configured = int(raw) if raw else 5
    except (TypeError, ValueError):
        configured = 5
    return max(1, min(configured, 25))


def _status_value(env_key: str, default: str) -> str:
    raw = os.getenv(env_key)
    if raw is None:
        return default
    cleaned = raw.strip()
    return cleaned or default


def _resolve_drive_input_folder(cfg: Any) -> str:
    for candidate in (
        os.getenv("PDF_INPUT_FOLDER_ID"),
        os.getenv("DRIVE_INPUT_FOLDER_ID"),
        getattr(cfg, "drive_input_folder_id", None),
    ):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    raise HTTPException(
        status_code=500, detail="Drive input folder ID is not configured"
    )


def _query_param(request: Request, key: str) -> str | None:
    params = getattr(request, "query_params", None)
    if not params:
        return None
    getter = getattr(params, "get", None)
    if callable(getter):
        try:
            return getter(key)
        except Exception:  # noqa: BLE001 - defensive for SimpleNamespace in tests
            return None
    if isinstance(params, dict):
        return params.get(key)
    return getattr(params, key, None)


def _require_internal_token(request: Request) -> None:
    expected = getattr(request.app.state, "internal_event_token", None)
    provided = (
        request.headers.get("x-internal-event-token")
        or request.headers.get("X-Internal-Event-Token")
        or _query_param(request, "internal_event_token")
        or _query_param(request, "token")
    )
    if not expected or not provided:
        raise HTTPException(status_code=401, detail="Missing or invalid internal token")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Missing or invalid internal token")


def _extract_trace_id(request: Request) -> str | None:
    trace_header = request.headers.get("X-Cloud-Trace-Context")
    if trace_header and "/" in trace_header:
        return trace_header.split("/", 1)[0]
    return request.headers.get("X-Request-ID")


def _clean_entry(value: str) -> str:
    return value.strip().lstrip("•*- ").strip()


def _normalise_lines(value: Any) -> List[str]:
    if value is None:
        return []  # pragma: no cover - explicit empty guard
    if isinstance(value, (list, tuple, set)):
        cleaned: List[str] = []
        for item in value:
            text = _clean_entry(str(item))
            if text:
                cleaned.append(text)
        return cleaned
    if isinstance(value, str):
        parts = [_clean_entry(part) for part in value.splitlines()]
        return [part for part in parts if part]
    text = _clean_entry(str(value))
    return [text] if text else []  # pragma: no cover - non-string fallbacks


def _to_text(value: Any, *, bullet: bool = False) -> str:
    if isinstance(value, (list, tuple, set)):
        cleaned = _normalise_lines(list(value))
        if not cleaned:
            return ""  # pragma: no cover - defensive guard
        if bullet:
            return "\n".join(f"- {item}" for item in cleaned)
        return "\n".join(cleaned)
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""  # pragma: no cover - defensive guard
    if bullet:
        parts = _normalise_lines(text)
        if not parts:
            return ""  # pragma: no cover - defensive guard
        return "\n".join(f"- {item}" for item in parts)
    return text


def _section(
    heading: str,
    value: Any,
    *,
    bullet: bool = False,
    fallback: str = "N/A",
) -> Tuple[str, str]:
    text = _to_text(value, bullet=bullet)
    return heading, text or fallback


def _assemble_sections(summarised: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Compose ordered PDF sections using canonical headings."""

    sections: List[Tuple[str, str]] = []
    seen_keys: set[str] = set()

    for heading, key, bullet, fallback in _SECTION_DEFINITIONS:
        sections.append(_section(heading, summarised.get(key), bullet=bullet, fallback=fallback))
        seen_keys.add(key)

    for heading, key, fallback in _LIST_DEFINITIONS:
        sections.append(
            _section(heading, summarised.get(key), bullet=True, fallback=fallback)
        )
        seen_keys.add(key)

    for key, value in summarised.items():
        if not isinstance(key, str):
            continue  # pragma: no cover - defensive guard
        if key in seen_keys or key.startswith("_"):
            continue
        text = _to_text(value)
        if not text:
            continue  # pragma: no cover - defensive guard
        sections.append((key.strip(), text))
    return sections


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return None  # pragma: no cover - non-bool strings are ignored


def _pdf_guard_enabled() -> bool:
    disabled = _parse_bool(os.getenv("PDF_GUARD_DISABLED"))
    if disabled:
        return False

    dev_override = _parse_bool(os.getenv("PDF_DEV_GUARD"))
    if dev_override is not None:
        return dev_override

    explicit = _parse_bool(os.getenv("PDF_GUARD_ENABLED"))
    if explicit is not None:
        return explicit

    env_name = os.getenv("ENVIRONMENT", "").strip().lower()
    if env_name in {"local", "dev", "test", "unit"}:
        return True
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True  # pragma: no cover - integration-only
    if _parse_bool(os.getenv("UNIT_TESTING")):
        return True
    return True


def _detect_forbidden_phrases(sections: Sequence[Tuple[str, str]]) -> List[str]:
    hits: List[str] = []
    for heading, body in sections:
        blob = f"{heading}\n{body}".lower()
        for phrase in FORBIDDEN_PDF_PHRASES:
            if phrase in blob:
                hits.append(phrase)
    return hits


def _validate_pdf_sections(
    sections: Sequence[Tuple[str, str]], *, guard_enabled: bool
) -> Tuple[bool, List[str]]:
    hits = _detect_forbidden_phrases(sections)
    if hits:
        structured_log(
            _API_LOG,
            logging.WARNING,
            "pdf_validation_forbidden_phrases_detected",
            phrases=hits,
        )
        if guard_enabled:
            raise HTTPException(
                status_code=500,
                detail="PDF validation failed due to forbidden phrases.",
            )
        return False, hits
    return True, []  # pragma: no cover - success path validated via pipeline


async def _invoke_pipeline(
    request: Request,
    *,
    pdf_bytes: bytes,
    source: str,
    trace_id: str | None,
) -> ProcessPipelineResult:
    pipeline = getattr(request.app.state, "process_pipeline", None)
    if pipeline is None:
        raise HTTPException(
            status_code=500, detail="Processing pipeline is not configured"
        )
    guard_enabled = _pdf_guard_enabled()
    try:
        return await pipeline.run(
            pdf_bytes=pdf_bytes,
            source=source,
            trace_id=trace_id,
            guard_enabled=guard_enabled,
            request_context={"path": str(request.url.path), "method": request.method},
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PdfValidationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except SummarizationError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail="Summary generation failed"
        ) from exc
    except PDFGenerationError as exc:
        raise HTTPException(status_code=500, detail="Failed to render PDF") from exc
    except DriveServiceError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail="Failed to upload PDF to Drive"
        ) from exc
    except OCRServiceError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail="Document AI processing failed"
        ) from exc


@router.get("/healthz", tags=["health"])
async def health_check(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@router.post("", tags=["process"])
async def process_pdf(request: Request, file: UploadFile) -> Response:
    pdf_bytes = await file.read()
    trace_id = _extract_trace_id(request)
    result = await _invoke_pipeline(
        request, pdf_bytes=pdf_bytes, source="upload", trace_id=trace_id
    )
    return Response(result.pdf_bytes, media_type="application/pdf")


@router.get("/drive", tags=["process"])
async def process_drive(
    request: Request, file_id: str = Query(..., min_length=1)
) -> JSONResponse:
    trace_id = _extract_trace_id(request)
    cfg = getattr(request.app.state, "config")
    try:
        pdf_bytes = request.app.state.drive_client.download_pdf(
            file_id,
            log_context={"trace_id": trace_id, "phase": "drive_download"},
            quota_project=getattr(cfg, "project_id", None),
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DriveServiceError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail="Failed to download file from Drive"
        ) from exc

    result = await _invoke_pipeline(
        request, pdf_bytes=pdf_bytes, source="drive", trace_id=trace_id
    )
    if result.drive_file_id is None:
        raise HTTPException(status_code=503, detail="Drive upload disabled")

    payload: Dict[str, Any] = {
        "report_file_id": result.drive_file_id,
        "supervisor_passed": bool(result.validation.get("supervisor_passed")),
        "request_id": uuid.uuid4().hex,
    }
    pdf_writer = getattr(request.app.state, "pdf_writer", None)
    writer_backend = getattr(getattr(pdf_writer, "backend", None), "__class__", None)
    if writer_backend is not None:
        payload["writer_backend"] = writer_backend.__name__
    if "pdf_compliant" in result.validation:
        payload["pdf_compliant"] = result.validation["pdf_compliant"]
    if "pdf_forbidden_phrases" in result.validation:
        payload["pdf_forbidden_phrases"] = result.validation["pdf_forbidden_phrases"]
    return JSONResponse(payload)


@router.post("/drive/poll", tags=["process"])
async def poll_drive_folder(
    request: Request,
    limit: int = Query(
        3,
        ge=1,
        le=25,
        description="Maximum Drive files to process in a single poll.",
    ),
) -> JSONResponse:
    _require_internal_token(request)
    cfg = getattr(request.app.state, "config")
    folder_id = _resolve_drive_input_folder(cfg)
    batch_limit = min(limit, _drive_poll_batch_limit())
    status_key = _status_value("DRIVE_POLL_STATUS_KEY", "mccStatus")
    completed_value = _status_value("DRIVE_POLL_COMPLETED_VALUE", "completed")
    processing_value = _status_value("DRIVE_POLL_PROCESSING_VALUE", "processing")
    failed_value = _status_value("DRIVE_POLL_FAILED_VALUE", "failed")
    try:
        pending = drive_client_module.list_pending_pdfs(
            folder_id,
            limit=batch_limit,
            status_key=status_key,
            completed_value=completed_value,
            processing_value=processing_value,
            failed_value=failed_value,
        )
    except Exception as exc:  # noqa: BLE001 - propagate structured failure
        structured_log(
            _API_LOG,
            logging.ERROR,
            "drive_poll_listing_failed",
            folder_id=folder_id,
            error=str(exc),
        )
        raise HTTPException(status_code=502, detail="Failed to list Drive files") from exc

    if not pending:
        structured_log(
            _API_LOG,
            logging.INFO,
            "drive_poll_idle",
            folder_id=folder_id,
            limit=batch_limit,
        )
        return JSONResponse(
            {
                "status": "idle",
                "processed": [],
                "errors": [],
                "skipped": [],
                "polled": 0,
                "folder_id": folder_id,
            }
        )

    drive_client = getattr(request.app.state, "drive_client", None)
    if drive_client is None:
        raise HTTPException(status_code=500, detail="Drive client not configured")
    project_id = getattr(cfg, "project_id", None)
    processed: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for entry in pending:
        file_id = str(entry.get("id") or "").strip()
        if not file_id:
            continue
        file_name = entry.get("name") or file_id
        trace_id = uuid.uuid4().hex
        masked_id = _mask_drive_id(file_id)
        if file_name.lower().startswith("summary-"):
            structured_log(
                _API_LOG,
                logging.INFO,
                "drive_poll_skip_summary_artifact",
                folder_id=folder_id,
                file_id=masked_id,
                file_name=file_name,
            )
            skipped.append({"file_id": file_id, "reason": "summary_artifact"})
            continue
        structured_log(
            _API_LOG,
            logging.INFO,
            "drive_poll_candidate",
            folder_id=folder_id,
            file_id=masked_id,
            file_name=file_name,
        )
        try:
            drive_client_module.update_app_properties(
                file_id,
                {
                    status_key: processing_value,
                    "mccUpdatedAt": _utc_timestamp(),
                },
            )
        except Exception as exc:  # noqa: BLE001 - Drive errors are surfaced in response payload
            structured_log(
                _API_LOG,
                logging.ERROR,
                "drive_poll_claim_failed",
                file_id=masked_id,
                error=str(exc),
            )
            failures.append({"file_id": file_id, "error": f"claim_failed: {exc}"})
            continue

        try:
            pdf_bytes = drive_client.download_pdf(
                file_id,
                log_context={
                    "trace_id": trace_id,
                    "phase": "drive_poll_download",
                    "folder_id": folder_id,
                },
                quota_project=project_id,
            )
            result = await _invoke_pipeline(
                request, pdf_bytes=pdf_bytes, source="drive-poll", trace_id=trace_id
            )
            report_id = result.drive_file_id
            if not report_id:
                raise RuntimeError("Drive upload disabled")
            drive_client_module.update_app_properties(
                file_id,
                {
                    status_key: completed_value,
                    "mccReportId": report_id,
                    "mccUpdatedAt": _utc_timestamp(),
                },
            )
            processed.append(
                {
                    "file_id": file_id,
                    "report_file_id": report_id,
                    "trace_id": trace_id,
                }
            )
            structured_log(
                _API_LOG,
                logging.INFO,
                "drive_poll_success",
                file_id=masked_id,
                report_file_id=_mask_drive_id(report_id),
                trace_id=trace_id,
            )
        except Exception as exc:  # noqa: BLE001
            drive_client_module.update_app_properties(
                file_id,
                {
                    status_key: failed_value,
                    "mccError": str(exc)[:180],
                    "mccUpdatedAt": _utc_timestamp(),
                },
            )
            structured_log(
                _API_LOG,
                logging.ERROR,
                "drive_poll_processing_failed",
                file_id=masked_id,
                trace_id=trace_id,
                error=str(exc),
            )
            failures.append({"file_id": file_id, "error": str(exc)})

    status_value = "processed"
    if failures and processed:
        status_value = "partial"
    elif failures and not processed:
        status_value = "failed"

    return JSONResponse(
        {
            "status": status_value,
            "processed": processed,
            "errors": failures,
            "skipped": skipped,
            "polled": len(pending),
            "folder_id": folder_id,
        }
    )
