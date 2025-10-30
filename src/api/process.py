"""Process routes for MCC OCR Summary FastAPI service."""

from __future__ import annotations

import inspect
import logging
import os
import uuid
from typing import Any, Dict, Tuple

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile
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

router = APIRouter()

_API_LOG = logging.getLogger("api")


def _extract_trace_id(request: Request) -> str | None:
    trace_header = request.headers.get("X-Cloud-Trace-Context")
    if trace_header and "/" in trace_header:
        return trace_header.split("/", 1)[0]
    return request.headers.get("X-Request-ID")


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
        raise HTTPException(
            status_code=502, detail="Document AI processing failed"
        ) from exc
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

    min_ocr_chars = int(os.getenv("MIN_OCR_CHARS", "50"))
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

    try:
        pdf_payload = app.state.pdf_writer.build(dict(summary_dict))
    except PDFGenerationError as exc:
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
    )

    return pdf_payload, validation, drive_file_id


@router.get("/healthz", tags=["health"])
async def health_check(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@router.post("", tags=["process"])
async def process_pdf(request: Request, file: UploadFile) -> Response:
    pdf_bytes = await file.read()
    payload, _validation, _drive_id = await _execute_pipeline(
        request, pdf_bytes=pdf_bytes, source="upload"
    )
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

    _payload, validation, drive_file_id = await _execute_pipeline(
        request, pdf_bytes=pdf_bytes, source="drive"
    )
    if drive_file_id is None:
        raise HTTPException(status_code=503, detail="Drive upload disabled")

    request_id = uuid.uuid4().hex
    return JSONResponse(
        {
            "report_file_id": drive_file_id,
            "supervisor_passed": bool(validation.get("supervisor_passed")),
            "request_id": request_id,
        }
    )
