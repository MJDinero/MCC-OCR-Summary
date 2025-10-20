"""Process routes for MCC OCR Summary FastAPI service."""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, Tuple

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from src.errors import ValidationError
from src.services.supervisor import CommonSenseSupervisor
from src.utils.summary_thresholds import compute_summary_min_chars

router = APIRouter()

_API_LOG = logging.getLogger("api")


async def _execute_pipeline(request: Request, *, pdf_bytes: bytes, source: str) -> Tuple[bytes, Dict[str, Any], str | None]:
    app = request.app
    cfg = getattr(app.state, "config")
    stub_mode: bool = getattr(app.state, "stub_mode", False)

    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file empty")
    if not stub_mode and not pdf_bytes.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    ocr_result = app.state.ocr_service.process(pdf_bytes)
    ocr_text = (ocr_result.get("text") or "").strip()
    ocr_len = len(ocr_text)
    pages = ocr_result.get("pages") or []
    _API_LOG.info(
        "ocr_complete",
        extra={
            "source": source,
            "text_length": ocr_len,
            "pages": len(pages),
        },
    )

    min_ocr_chars = int(os.getenv("MIN_OCR_CHARS", "50"))
    if ocr_len < min_ocr_chars:
        _API_LOG.error(
            "ocr_too_short",
            extra={
                "source": source,
                "text_length": ocr_len,
                "min_required": min_ocr_chars,
            },
        )
        raise HTTPException(status_code=422, detail="OCR extraction insufficient")

    summary_raw = await app.state.summariser.summarise_async(ocr_text)
    summary_dict = summary_raw if isinstance(summary_raw, dict) else {"Medical Summary": str(summary_raw or "")}
    summary_text_fragments = [value for value in summary_dict.values() if isinstance(value, str)]
    summary_text = "\n".join(summary_text_fragments).strip()
    summary_len = len(summary_text)
    min_summary_chars = compute_summary_min_chars(ocr_len, stub_mode=stub_mode)
    if summary_len < min_summary_chars:
        _API_LOG.error(
            "summary_too_short",
            extra={
                "summary_length": summary_len,
                "min_required": min_summary_chars,
                "ocr_length": ocr_len,
                "source": source,
            },
        )
        raise HTTPException(status_code=502, detail="Summary generation failed")

    supervisor_flag = getattr(app.state, "supervisor_simple", False)
    supervisor = CommonSenseSupervisor(simple=supervisor_flag)
    doc_stats = {
        "pages": len(pages),
        "text_length": ocr_len,
        "file_size_mb": round(len(pdf_bytes) / (1024 * 1024), 3),
    }
    validation = supervisor.validate(ocr_text=ocr_text, summary=summary_dict, doc_stats=doc_stats)
    if not validation.get("supervisor_passed"):
        _API_LOG.warning("supervisor_basic_check_failed", extra={"source": source, **validation})

    pdf_payload = app.state.pdf_writer.build(dict(summary_dict))
    write_to_drive = os.getenv("WRITE_TO_DRIVE", "true").strip().lower() == "true"

    drive_file_id: str | None = None
    if write_to_drive:
        folder_id = os.getenv("DRIVE_REPORT_FOLDER_ID", cfg.drive_report_folder_id)
        try:
            drive_file_id = app.state.drive_client.upload_pdf(pdf_payload, folder_id)
        except Exception as drive_exc:  # pragma: no cover
            _API_LOG.error("drive_upload_failed", extra={"error": str(drive_exc), "source": source})
            if not stub_mode:
                raise

    _API_LOG.info(
        "process_complete",
        extra={
            "source": source,
            "supervisor_passed": validation.get("supervisor_passed"),
            "summary_chars": len(summary_text),
            "pdf_bytes": len(pdf_payload),
        },
    )

    return pdf_payload, validation, drive_file_id


@router.post("/process", tags=["process"])
async def process_pdf(request: Request, file: UploadFile) -> Response:
    pdf_bytes = await file.read()
    payload, _validation, _drive_id = await _execute_pipeline(request, pdf_bytes=pdf_bytes, source="upload")
    return Response(payload, media_type="application/pdf")


@router.get("/process_drive", tags=["process"])
async def process_drive(request: Request, file_id: str = Query(..., min_length=1)) -> JSONResponse:
    try:
        pdf_bytes = request.app.state.drive_client.download_pdf(file_id)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - external service
        _API_LOG.error("drive_download_failed", extra={"error": str(exc), "file_id": file_id})
        raise HTTPException(status_code=502, detail="Failed to download file from Drive") from exc

    _payload, validation, drive_file_id = await _execute_pipeline(request, pdf_bytes=pdf_bytes, source="drive")
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
