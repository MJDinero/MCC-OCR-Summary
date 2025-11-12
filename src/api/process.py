"""Process routes for MCC OCR Summary FastAPI service."""

from __future__ import annotations

import inspect
import logging
import os
import uuid
from typing import Any, Dict, Tuple, List, Sequence

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse, Response

from src.errors import (
    PDFGenerationError,
    ValidationError,
    OCRServiceError,
    DriveServiceError,
    SummarizationError,
)
from src.services.docai_helper import clean_ocr_output
from src.services.supervisor import CommonSenseSupervisor
from src.utils.summary_thresholds import compute_summary_min_chars
from src.utils.logging_utils import structured_log
from src.utils.pipeline_failures import publish_pipeline_failure

router = APIRouter()

_API_LOG = logging.getLogger("api")
_FORBIDDEN_PDF_PHRASES = (
    "(condensed)",
    "structured indices",
    "summary lists",
    "document processed in",
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
        return []
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
    return [text] if text else []


def _to_text(value: Any, *, bullet: bool = False) -> str:
    if isinstance(value, (list, tuple, set)):
        cleaned = _normalise_lines(list(value))
        if not cleaned:
            return ""
        if bullet:
            return "\n".join(f"- {item}" for item in cleaned)
        return "\n".join(cleaned)
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if bullet:
        parts = _normalise_lines(text)
        if not parts:
            return ""
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
    text = text or fallback
    return heading, text


SECTION_FALLBACK = "No clinically meaningful content was extracted for this section."
LIST_FALLBACK = "No clinically meaningful entries were extracted for this list."


def _assemble_sections(summarised: Dict[str, Any]) -> List[Tuple[str, str]]:
    sections: List[Tuple[str, str]] = []
    sections.append(
        _section(
            "Intro Overview",
            _normalise_lines(
                summarised.get("intro_overview") or summarised.get("overview")
            ),
            fallback=SECTION_FALLBACK,
        )
    )
    sections.append(
        _section(
            "Key Points",
            _normalise_lines(summarised.get("key_points")),
            bullet=True,
            fallback=SECTION_FALLBACK,
        )
    )
    sections.append(
        _section(
            "Detailed Findings",
            _normalise_lines(
                summarised.get("detailed_findings")
                or summarised.get("clinical_details")
            ),
            bullet=True,
            fallback=SECTION_FALLBACK,
        )
    )
    sections.append(
        _section(
            "Care Plan & Follow-Up",
            _normalise_lines(summarised.get("care_plan")),
            bullet=True,
            fallback=SECTION_FALLBACK,
        )
    )
    optional_lists = [
        (
            "Diagnoses",
            _normalise_lines(summarised.get("_diagnoses_list")),
        ),
        (
            "Providers",
            _normalise_lines(summarised.get("_providers_list")),
        ),
        (
            "Medications / Prescriptions",
            _normalise_lines(summarised.get("_medications_list")),
        ),
    ]
    for heading, lines in optional_lists:
        sections.append(
            _section(heading, lines, bullet=True, fallback=LIST_FALLBACK)
        )
    return sections


def _pdf_guard_enabled() -> bool:
    explicit = os.getenv("PDF_DEV_GUARD")
    if explicit is not None:
        return explicit.strip().lower() in {"1", "true", "yes", "on"}
    env_name = os.getenv("ENVIRONMENT", "").strip().lower()
    if env_name in {"local", "dev", "test", "unit"}:
        return True
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True
    testing_flag = os.getenv("UNIT_TESTING", "")
    if isinstance(testing_flag, str) and testing_flag.lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return True
    return False


def _detect_forbidden_phrases(sections: Sequence[Tuple[str, str]]) -> List[str]:
    hits: List[str] = []
    for heading, body in sections:
        blob = f"{heading}\n{body}".lower()
        for phrase in _FORBIDDEN_PDF_PHRASES:
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
            forbidden_phrases=hits,
            guard_enabled=guard_enabled,
        )
        if guard_enabled:
            raise HTTPException(
                status_code=500,
                detail=(
                    "PDF validation guard blocked forbidden phrases: "
                    + ", ".join(sorted(set(hits)))
                ),
            )
    return (not hits, hits)


async def _execute_pipeline(
    request: Request, *, pdf_bytes: bytes, source: str
) -> Tuple[bytes, Dict[str, Any], str | None]:
    app = request.app
    cfg = getattr(app.state, "config")
    stub_mode: bool = getattr(app.state, "stub_mode", False)
    trace_id = _extract_trace_id(request)

    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file empty")
    if not stub_mode and not pdf_bytes.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    ocr_service = app.state.ocr_service
    process_params = inspect.signature(ocr_service.process).parameters  # type: ignore[attr-defined]
    ocr_kwargs = {}
    if "trace_id" in process_params:
        ocr_kwargs["trace_id"] = trace_id
    try:
        ocr_result = ocr_service.process(pdf_bytes, **ocr_kwargs)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OCRServiceError as exc:
        structured_log(
            _API_LOG,
            logging.ERROR,
            "ocr_failure",
            trace_id=trace_id,
            source=source,
            error=str(exc),
        )
        raise
    ocr_text = (ocr_result.get("text") or "").strip()
    ocr_len = len(ocr_text)
    pages = ocr_result.get("pages") or []
    structured_log(
        _API_LOG,
        logging.INFO,
        "ocr_success",
        trace_id=trace_id,
        source=source,
        text_length=ocr_len,
        pages=len(pages),
    )

    min_ocr_chars = 0 if stub_mode else int(os.getenv("MIN_OCR_CHARS", "50"))
    if ocr_len < min_ocr_chars:
        structured_log(
            _API_LOG,
            logging.ERROR,
            "ocr_too_short",
            trace_id=trace_id,
            source=source,
            text_length=ocr_len,
            min_required=min_ocr_chars,
        )
        raise HTTPException(status_code=422, detail="OCR extraction insufficient")

    cleaned_ocr_text = clean_ocr_output(ocr_text)
    summary_source_text = cleaned_ocr_text or ocr_text
    try:
        summary_raw = await app.state.summariser.summarise_async(summary_source_text)
    except SummarizationError as exc:
        publish_pipeline_failure(
            stage="SUMMARY_JOB",
            job_id=None,
            trace_id=trace_id,
            error=exc,
            metadata={"source": source},
        )
        structured_log(
            _API_LOG,
            logging.ERROR,
            "summary_failure",
            trace_id=trace_id,
            source=source,
            error=str(exc),
        )
        raise HTTPException(
            status_code=502, detail="Summary generation failed"
        ) from exc
    summary_dict = (
        summary_raw
        if isinstance(summary_raw, dict)
        else {"Medical Summary": str(summary_raw or "")}
    )
    summary_text_fragments = [
        value for value in summary_dict.values() if isinstance(value, str)
    ]
    summary_text = "\n".join(summary_text_fragments).strip()
    summary_len = len(summary_text)
    min_summary_chars = compute_summary_min_chars(ocr_len, stub_mode=stub_mode)
    if summary_len < min_summary_chars:
        structured_log(
            _API_LOG,
            logging.ERROR,
            "summary_too_short",
            trace_id=trace_id,
            summary_length=summary_len,
            min_required=min_summary_chars,
            ocr_length=ocr_len,
            source=source,
        )
        raise HTTPException(status_code=502, detail="Summary generation failed")

    supervisor_flag = getattr(app.state, "supervisor_simple", False)
    supervisor = CommonSenseSupervisor(simple=supervisor_flag)
    doc_stats = {
        "pages": len(pages),
        "text_length": ocr_len,
        "file_size_mb": round(len(pdf_bytes) / (1024 * 1024), 3),
    }
    validation = supervisor.validate(
        ocr_text=ocr_text, summary=summary_dict, doc_stats=doc_stats
    )
    if not validation.get("supervisor_passed"):
        structured_log(
            _API_LOG,
            logging.WARNING,
            "supervisor_basic_check_failed",
            trace_id=trace_id,
            source=source,
            **validation,
        )

    summarised: Dict[str, Any] = dict(summary_dict)

    title = (
        ocr_result.get("title")
        or summarised.get("title")
        or summarised.get("document_title")
        or "Medical Summary"
    )
    sections = _assemble_sections(summarised)
    guard_enabled = _pdf_guard_enabled()
    pdf_compliant, forbidden_hits = _validate_pdf_sections(
        sections, guard_enabled=guard_enabled
    )
    validation["pdf_compliant"] = pdf_compliant
    if forbidden_hits:
        validation["pdf_forbidden_phrases"] = forbidden_hits

    try:
        pdf_payload = app.state.pdf_writer.build(title, sections)
    except PDFGenerationError as exc:
        publish_pipeline_failure(
            stage="PDF_JOB",
            job_id=None,
            trace_id=trace_id,
            error=exc,
            metadata={"source": source},
        )
        structured_log(
            _API_LOG,
            logging.ERROR,
            "pdf_generation_failed",
            trace_id=trace_id,
            source=source,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail="Failed to render PDF") from exc
    write_to_drive = os.getenv("WRITE_TO_DRIVE", "true").strip().lower() == "true"

    drive_file_id: str | None = None
    if write_to_drive:
        folder_id = os.getenv("DRIVE_REPORT_FOLDER_ID", cfg.drive_report_folder_id)
        try:
            drive_file_id = app.state.drive_client.upload_pdf(
                pdf_payload,
                folder_id,
                log_context={"trace_id": trace_id, "source": source},
            )
        except DriveServiceError as drive_exc:
            structured_log(
                _API_LOG,
                logging.ERROR,
                "drive_upload_failed",
                trace_id=trace_id,
                source=source,
                error=str(drive_exc),
            )
            if not stub_mode:
                raise HTTPException(
                    status_code=502, detail="Failed to upload PDF to Drive"
                ) from drive_exc

    structured_log(
        _API_LOG,
        logging.INFO,
        "process_complete",
        trace_id=trace_id,
        source=source,
        supervisor_passed=validation.get("supervisor_passed"),
        summary_chars=len(summary_text),
        pdf_bytes=len(pdf_payload),
        pdf_compliant=pdf_compliant,
        forbidden_phrases=forbidden_hits if forbidden_hits else None,
    )

    return pdf_payload, validation, drive_file_id


@router.get("/healthz", tags=["health"])
async def health_check(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@router.post("", tags=["process"])
async def process_pdf(request: Request, file: UploadFile) -> Response:
    pdf_bytes = await file.read()
    try:
        payload, _validation, _drive_id = await _execute_pipeline(
            request, pdf_bytes=pdf_bytes, source="upload"
        )
    except OCRServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Document AI processing failed",
        ) from exc
    return Response(payload, media_type="application/pdf")


@router.get("/drive", tags=["process"])
async def process_drive(
    request: Request, file_id: str = Query(..., min_length=1)
) -> JSONResponse:
    trace_id = _extract_trace_id(request)
    cfg = request.app.state.config
    try:
        pdf_bytes = request.app.state.drive_client.download_pdf(
            file_id,
            log_context={"trace_id": trace_id, "phase": "drive_download"},
            quota_project=getattr(cfg, "project_id", None),
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DriveServiceError as exc:
        structured_log(
            _API_LOG,
            logging.ERROR,
            "drive_download_failed",
            trace_id=trace_id,
            file_id=file_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=502, detail="Failed to download file from Drive"
        ) from exc

    try:
        _payload, validation, drive_file_id = await _execute_pipeline(
            request, pdf_bytes=pdf_bytes, source="drive"
        )
    except OCRServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Document AI processing failed",
        ) from exc
    if drive_file_id is None:
        raise HTTPException(status_code=503, detail="Drive upload disabled")

    request_id = uuid.uuid4().hex
    response_payload: Dict[str, Any] = {
        "report_file_id": drive_file_id,
        "supervisor_passed": bool(validation.get("supervisor_passed")),
        "request_id": request_id,
        "compose_mode": getattr(request.app.state, "summary_compose_mode", "unknown"),
    }
    response_payload["pdf_compliant"] = bool(validation.get("pdf_compliant", True))
    if not response_payload["pdf_compliant"]:
        response_payload["pdf_forbidden_phrases"] = validation.get(
            "pdf_forbidden_phrases", []
        )
    writer_backend = getattr(
        request.app.state,
        "writer_backend",
        getattr(request.app.state, "pdf_writer_mode", None),
    )
    if writer_backend:
        response_payload["writer_backend"] = writer_backend

    # Diagnostics to confirm compose/writer at runtime - errors must not break API
    try:
        if "writer_backend" not in response_payload:
            backend_cls = getattr(
                request.app.state.pdf_writer.backend,
                "__class__",
                type("Unknown", (), {}),
            )
            response_payload["writer_backend"] = backend_cls.__name__
    except Exception:  # pragma: no cover
        pass

    return JSONResponse(response_payload)
