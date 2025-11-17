"""Process routes for MCC OCR Summary FastAPI service."""

from __future__ import annotations

import logging
import os
import uuid
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
