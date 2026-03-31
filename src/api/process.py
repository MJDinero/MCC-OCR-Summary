"""Process routes for MCC OCR Summary FastAPI service."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import uuid
from typing import Any, Dict, Mapping, Tuple

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from src.errors import (
    PDFGenerationError,
    ValidationError,
    OCRServiceError,
    DriveServiceError,
    SummarizationError,
)
from src.models.summary_contract import SummaryContract
from src.services.drive_bridge import mirror_drive_pdf_to_intake
from src.services.docai_helper import clean_ocr_output
from src.services.summariser_refactored import _prepare_summary_source
from src.services.summary_input_preparer import prepare_summary_input_from_pdf_bytes
from src.services.supervisor import CommonSenseSupervisor
from src.utils.summary_thresholds import compute_summary_min_chars
from src.utils.logging_utils import log_stage_skipped, stage_marker, structured_log

router = APIRouter()

_API_LOG = logging.getLogger("api")
_PIPELINE_COMPONENT = "process_api"


def _extract_correlation_ids(request: Request) -> tuple[str | None, str | None]:
    trace_id: str | None = None
    trace_header = request.headers.get("X-Cloud-Trace-Context")
    if trace_header and "/" in trace_header:
        trace_id = trace_header.split("/", 1)[0]
    request_id = request.headers.get("X-Request-ID")
    if not trace_id:
        trace_id = request_id
    return trace_id, request_id


def _resolve_drive_poll_limit() -> int:
    raw_limit = os.getenv("DRIVE_POLL_MAX_FILES", "10").strip()
    try:
        limit = int(raw_limit)
    except ValueError as exc:
        raise HTTPException(
            status_code=500, detail="Invalid DRIVE_POLL_MAX_FILES configuration"
        ) from exc
    if limit < 1 or limit > 100:
        raise HTTPException(
            status_code=500, detail="DRIVE_POLL_MAX_FILES must be between 1 and 100"
        )
    return limit


def _summary_validation_reasons(validation: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    reason_value = validation.get("reason")
    if isinstance(reason_value, str):
        reasons.extend(
            token.strip() for token in reason_value.split(",") if token.strip()
        )
    quality = validation.get("quality")
    if isinstance(quality, Mapping):
        quality_reasons = quality.get("reasons")
        if isinstance(quality_reasons, list):
            reasons.extend(
                str(token).strip() for token in quality_reasons if str(token).strip()
            )
    deduped: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        if reason in seen:
            continue
        seen.add(reason)
        deduped.append(reason)
    return deduped


def _update_summary_metadata(
    summary: Mapping[str, Any], metadata_patch: Mapping[str, Any]
) -> Dict[str, Any]:
    contract = SummaryContract.from_mapping(summary)
    metadata = dict(contract.metadata)
    for key, value in metadata_patch.items():
        if value is None:
            continue
        metadata[key] = value
    contract.metadata = metadata
    return contract.to_dict()


def _resolve_supervisor_alignment_source(
    summary_source_text: str,
    doc_metadata: Mapping[str, Any] | None,
) -> str:
    if not summary_source_text or not isinstance(doc_metadata, Mapping):
        return summary_source_text
    try:
        alignment_source, _ = _prepare_summary_source(
            summary_source_text,
            dict(doc_metadata),
        )
    except Exception:
        return summary_source_text
    return alignment_source or summary_source_text


async def _execute_pipeline(
    request: Request, *, pdf_bytes: bytes, source: str
) -> Tuple[bytes, Dict[str, Any], str | None]:
    app = request.app
    cfg = getattr(app.state, "config")
    stub_mode: bool = getattr(app.state, "stub_mode", False)
    trace_id, request_id = _extract_correlation_ids(request)

    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file empty")
    if not stub_mode and not pdf_bytes.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    with stage_marker(
        _API_LOG,
        stage="triage",
        trace_id=trace_id,
        request_id=request_id,
        source=source,
        component=_PIPELINE_COMPONENT,
    ) as triage_stage:
        prepared_input = prepare_summary_input_from_pdf_bytes(
            pdf_bytes,
            job_metadata={"document_id": request_id, "source": source},
        )
        triage_stage.add_completion_fields(
            requires_ocr=prepared_input.requires_ocr,
            summary_text_source=prepared_input.text_source,
            triage_reason=prepared_input.route_reason,
            native_chars=len(prepared_input.text),
            native_pages=len(prepared_input.pages),
        )

    ocr_text = prepared_input.text
    pages = list(prepared_input.pages)
    if prepared_input.requires_ocr:
        ocr_service = app.state.ocr_service
        process_params = inspect.signature(ocr_service.process).parameters  # type: ignore[attr-defined]
        ocr_kwargs = {}
        if "trace_id" in process_params:
            ocr_kwargs["trace_id"] = trace_id
        try:
            with stage_marker(
                _API_LOG,
                stage="ocr",
                trace_id=trace_id,
                request_id=request_id,
                source=source,
                component=_PIPELINE_COMPONENT,
            ) as ocr_stage:
                ocr_result = ocr_service.process(pdf_bytes, **ocr_kwargs)
                ocr_text = (ocr_result.get("text") or "").strip()
                pages = ocr_result.get("pages") or []
                ocr_stage.add_completion_fields(
                    text_length=len(ocr_text), pages=len(pages)
                )
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OCRServiceError as exc:
            structured_log(
                _API_LOG,
                logging.ERROR,
                "ocr_failure",
                trace_id=trace_id,
                request_id=request_id,
                source=source,
                error_type=type(exc).__name__,
            )
            raise HTTPException(
                status_code=502, detail="Document AI processing failed"
            ) from exc
        structured_log(
            _API_LOG,
            logging.INFO,
            "ocr_success",
            trace_id=trace_id,
            request_id=request_id,
            source=source,
            text_length=len(ocr_text),
            pages=len(pages),
        )
    else:
        log_stage_skipped(
            _API_LOG,
            stage="ocr",
            reason=prepared_input.route_reason,
            trace_id=trace_id,
            request_id=request_id,
            source=source,
            component=_PIPELINE_COMPONENT,
        )

    source_text_len = len(ocr_text)
    min_ocr_chars = int(os.getenv("MIN_OCR_CHARS", "50"))
    if source_text_len < min_ocr_chars:
        structured_log(
            _API_LOG,
            logging.ERROR,
            "ocr_too_short",
            trace_id=trace_id,
            request_id=request_id,
            source=source,
            text_length=source_text_len,
            min_required=min_ocr_chars,
            summary_text_source=prepared_input.text_source,
        )
        raise HTTPException(status_code=422, detail="OCR extraction insufficient")

    if prepared_input.requires_ocr:
        with stage_marker(
            _API_LOG,
            stage="split",
            trace_id=trace_id,
            request_id=request_id,
            source=source,
            component=_PIPELINE_COMPONENT,
        ) as split_stage:
            cleaned_ocr_text = clean_ocr_output(ocr_text)
            split_stage.add_completion_fields(
                cleaned_text_length=len(cleaned_ocr_text or "")
            )
        summary_source_text = cleaned_ocr_text or ocr_text
    else:
        log_stage_skipped(
            _API_LOG,
            stage="split",
            reason="native_text_selected",
            trace_id=trace_id,
            request_id=request_id,
            source=source,
            component=_PIPELINE_COMPONENT,
        )
        summary_source_text = ocr_text

    summary_payload: Dict[str, Any] = {}
    summary_text = ""
    summary_len = 0
    summary_result: Any | None = None
    doc_metadata_payload: Dict[str, Any] = {
        "pages": pages,
        "document_id": request_id,
        "source": source,
        **prepared_input.metadata_patch,
    }
    try:
        async with stage_marker(
            _API_LOG,
            stage="summarisation",
            trace_id=trace_id,
            request_id=request_id,
            source=source,
            component=_PIPELINE_COMPONENT,
        ) as summary_stage:
            summariser = app.state.summariser
            if hasattr(summariser, "summarise_with_details"):
                summary_result = await asyncio.to_thread(
                    summariser.summarise_with_details,
                    summary_source_text,
                    doc_metadata=doc_metadata_payload,
                )
                summary_raw = summary_result.summary
            else:
                summary_raw = await summariser.summarise_async(
                    summary_source_text,
                    doc_metadata=doc_metadata_payload,
                )
            contract = SummaryContract.from_mapping(summary_raw)
            summary_payload = contract.to_dict()
            summary_text = contract.as_text()
            summary_len = len(summary_text)
            summary_stage.add_completion_fields(summary_chars=summary_len)
    except SummarizationError as exc:
        structured_log(
            _API_LOG,
            logging.ERROR,
            "summary_failure",
            trace_id=trace_id,
            request_id=request_id,
            source=source,
            error_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=502, detail="Summary generation failed"
        ) from exc
    min_summary_chars = compute_summary_min_chars(source_text_len, stub_mode=stub_mode)
    if summary_len < min_summary_chars:
        structured_log(
            _API_LOG,
            logging.ERROR,
            "summary_too_short",
            trace_id=trace_id,
            request_id=request_id,
            summary_length=summary_len,
            min_required=min_summary_chars,
            ocr_length=source_text_len,
            source=source,
        )
        raise HTTPException(status_code=502, detail="Summary generation failed")

    supervisor_flag = getattr(app.state, "supervisor_simple", False)
    supervisor = CommonSenseSupervisor(simple=supervisor_flag)
    supervisor_alignment_source = _resolve_supervisor_alignment_source(
        summary_source_text,
        doc_metadata_payload,
    )
    doc_stats = {
        "pages": len(pages),
        "text_length": source_text_len,
        "file_size_mb": round(len(pdf_bytes) / (1024 * 1024), 3),
    }
    with stage_marker(
        _API_LOG,
        stage="supervisor",
        trace_id=trace_id,
        request_id=request_id,
        source=source,
        component=_PIPELINE_COMPONENT,
    ) as supervisor_stage:
        validation = supervisor.validate(
            ocr_text=ocr_text,
            alignment_source_text=supervisor_alignment_source,
            summary=summary_payload,
            doc_stats=doc_stats,
        )
        supervisor_stage.add_completion_fields(
            supervisor_passed=bool(validation.get("supervisor_passed"))
        )
    initial_route = getattr(summary_result, "route", None)
    initial_route_reason = getattr(initial_route, "reason", None)
    initial_selected_strategy = getattr(initial_route, "selected_strategy", None)
    if initial_route is not None:
        summary_payload = _update_summary_metadata(
            summary_payload,
            {
                "summary_fast_lane_attempted": initial_selected_strategy == "one_shot",
                "summary_fast_lane_rejected": False,
                "summary_heavy_lane_triggered": initial_selected_strategy == "chunked",
                "summary_heavy_lane_retry_reason": initial_route_reason
                if initial_selected_strategy == "chunked"
                else None,
            },
        )
    if not validation.get("supervisor_passed"):
        fast_lane_reasons = _summary_validation_reasons(validation)
        if (
            summary_result is not None
            and initial_selected_strategy == "one_shot"
            and hasattr(app.state.summariser, "chunked_summariser")
        ):
            structured_log(
                _API_LOG,
                logging.WARNING,
                "fast_lane_rejected_chunk_fallback",
                trace_id=trace_id,
                request_id=request_id,
                source=source,
                fast_lane_rejection_reason=validation.get("reason"),
                fast_lane_rejection_reasons=fast_lane_reasons,
            )
            try:
                with stage_marker(
                    _API_LOG,
                    stage="summarisation_fallback",
                    trace_id=trace_id,
                    request_id=request_id,
                    source=source,
                    component=_PIPELINE_COMPONENT,
                ) as fallback_stage:
                    chunked_summary = await asyncio.to_thread(
                        app.state.summariser.chunked_summariser.summarise,
                        summary_source_text,
                        doc_metadata=doc_metadata_payload,
                    )
                    chunked_summary = _update_summary_metadata(
                        chunked_summary,
                        {
                            "summary_strategy_requested": summary_payload.get(
                                "metadata", {}
                            ).get("summary_strategy_requested"),
                            "summary_strategy_selected": summary_payload.get(
                                "metadata", {}
                            ).get("summary_strategy_selected"),
                            "summary_strategy_used": "chunked",
                            "summary_route_reason": summary_payload.get(
                                "metadata", {}
                            ).get("summary_route_reason"),
                            "summary_route_metrics": summary_payload.get(
                                "metadata", {}
                            ).get("summary_route_metrics"),
                            "summary_one_shot_token_threshold": summary_payload.get(
                                "metadata", {}
                            ).get("summary_one_shot_token_threshold"),
                            "summary_one_shot_max_pages": summary_payload.get(
                                "metadata", {}
                            ).get("summary_one_shot_max_pages"),
                            "summary_fallback_reason": "one_shot_supervisor_validation_failed",
                            "summary_fast_lane_attempted": True,
                            "summary_fast_lane_rejected": True,
                            "summary_fast_lane_rejection_reason": validation.get(
                                "reason"
                            )
                            or "fast_lane_confidence_low",
                            "summary_fast_lane_rejection_reasons": fast_lane_reasons,
                            "summary_heavy_lane_triggered": True,
                            "summary_heavy_lane_retry_reason": "fast_lane_confidence_low",
                            "summary_heavy_lane_retry_reasons": fast_lane_reasons,
                        },
                    )
                    contract = SummaryContract.from_mapping(chunked_summary)
                    summary_payload = contract.to_dict()
                    summary_text = contract.as_text()
                    summary_len = len(summary_text)
                    fallback_stage.add_completion_fields(summary_chars=summary_len)
            except SummarizationError as exc:
                structured_log(
                    _API_LOG,
                    logging.ERROR,
                    "summary_fallback_failure",
                    trace_id=trace_id,
                    request_id=request_id,
                    source=source,
                    error_type=type(exc).__name__,
                )
                raise HTTPException(
                    status_code=502, detail="Summary generation failed"
                ) from exc

            with stage_marker(
                _API_LOG,
                stage="supervisor",
                trace_id=trace_id,
                request_id=request_id,
                source=source,
                component=_PIPELINE_COMPONENT,
            ) as fallback_supervisor_stage:
                validation = supervisor.validate(
                    ocr_text=ocr_text,
                    alignment_source_text=supervisor_alignment_source,
                    summary=summary_payload,
                    doc_stats=doc_stats,
                    retries=0,
                    attempt_label="chunked_fallback",
                )
                fallback_supervisor_stage.add_completion_fields(
                    supervisor_passed=bool(validation.get("supervisor_passed"))
                )
            if not validation.get("supervisor_passed") and not supervisor.simple:
                retry_result = supervisor.retry_and_merge(
                    summariser=app.state.summariser.chunked_summariser,
                    ocr_text=summary_source_text,
                    alignment_source_text=supervisor_alignment_source,
                    doc_stats=doc_stats,
                    initial_summary=summary_payload,
                    initial_validation=validation,
                    doc_metadata=doc_metadata_payload,
                )
                validation = retry_result.validation
                summary_payload = _update_summary_metadata(
                    retry_result.summary,
                    {
                        "summary_strategy_requested": summary_payload.get(
                            "metadata", {}
                        ).get("summary_strategy_requested"),
                        "summary_strategy_selected": summary_payload.get(
                            "metadata", {}
                        ).get("summary_strategy_selected"),
                        "summary_strategy_used": "chunked",
                        "summary_route_reason": summary_payload.get("metadata", {}).get(
                            "summary_route_reason"
                        ),
                        "summary_route_metrics": summary_payload.get(
                            "metadata", {}
                        ).get("summary_route_metrics"),
                        "summary_one_shot_token_threshold": summary_payload.get(
                            "metadata", {}
                        ).get("summary_one_shot_token_threshold"),
                        "summary_one_shot_max_pages": summary_payload.get(
                            "metadata", {}
                        ).get("summary_one_shot_max_pages"),
                        "summary_fallback_reason": "one_shot_supervisor_validation_failed",
                        "summary_fast_lane_attempted": True,
                        "summary_fast_lane_rejected": True,
                        "summary_fast_lane_rejection_reason": validation.get("reason")
                        or "fast_lane_confidence_low",
                        "summary_fast_lane_rejection_reasons": fast_lane_reasons,
                        "summary_heavy_lane_triggered": True,
                        "summary_heavy_lane_retry_reason": "fast_lane_confidence_low",
                        "summary_heavy_lane_retry_reasons": fast_lane_reasons,
                    },
                )
                summary_text = SummaryContract.from_mapping(summary_payload).as_text()
                summary_len = len(summary_text)
        else:
            summary_payload = _update_summary_metadata(
                summary_payload,
                {
                    "summary_fast_lane_attempted": initial_selected_strategy == "one_shot",
                    "summary_fast_lane_rejected": initial_selected_strategy
                    == "one_shot",
                    "summary_fast_lane_rejection_reason": validation.get("reason"),
                    "summary_fast_lane_rejection_reasons": fast_lane_reasons,
                    "summary_heavy_lane_triggered": initial_selected_strategy
                    == "chunked",
                    "summary_heavy_lane_retry_reason": initial_route_reason
                    if initial_selected_strategy == "chunked"
                    else None,
                },
            )
    if not validation.get("supervisor_passed"):
        structured_log(
            _API_LOG,
            logging.WARNING,
            "supervisor_basic_check_failed",
            trace_id=trace_id,
            request_id=request_id,
            source=source,
            **validation,
        )
        raise HTTPException(status_code=502, detail="Summary rejected by quality gate")

    try:
        with stage_marker(
            _API_LOG,
            stage="pdf_write",
            trace_id=trace_id,
            request_id=request_id,
            source=source,
            component=_PIPELINE_COMPONENT,
        ) as pdf_stage:
            pdf_payload = app.state.pdf_writer.build(dict(summary_payload))
            pdf_stage.add_completion_fields(pdf_bytes=len(pdf_payload))
    except PDFGenerationError as exc:
        structured_log(
            _API_LOG,
            logging.ERROR,
            "pdf_generation_failed",
            trace_id=trace_id,
            request_id=request_id,
            source=source,
            error_type=type(exc).__name__,
        )
        raise HTTPException(status_code=500, detail="Failed to render PDF") from exc
    write_to_drive = os.getenv("WRITE_TO_DRIVE", "true").strip().lower() == "true"

    drive_file_id: str | None = None
    if write_to_drive:
        folder_id = os.getenv("DRIVE_REPORT_FOLDER_ID", cfg.drive_report_folder_id)
        try:
            with stage_marker(
                _API_LOG,
                stage="drive_upload",
                trace_id=trace_id,
                request_id=request_id,
                source=source,
                component=_PIPELINE_COMPONENT,
            ) as upload_stage:
                drive_file_id = app.state.drive_client.upload_pdf(
                    pdf_payload,
                    folder_id,
                    log_context={
                        "trace_id": trace_id,
                        "request_id": request_id,
                        "source": source,
                    },
                )
                if drive_file_id:
                    upload_stage.add_completion_fields(drive_file_id=drive_file_id)
        except DriveServiceError as drive_exc:
            structured_log(
                _API_LOG,
                logging.ERROR,
                "drive_upload_failed",
                trace_id=trace_id,
                request_id=request_id,
                source=source,
                error=str(drive_exc),
            )
            if not stub_mode:
                raise HTTPException(
                    status_code=502, detail="Failed to upload PDF to Drive"
                ) from drive_exc
    else:
        log_stage_skipped(
            _API_LOG,
            stage="drive_upload",
            reason="disabled",
            trace_id=trace_id,
            request_id=request_id,
            source=source,
            component=_PIPELINE_COMPONENT,
        )

    structured_log(
        _API_LOG,
        logging.INFO,
        "process_complete",
        trace_id=trace_id,
        request_id=request_id,
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
    trace_id, request_id = _extract_correlation_ids(request)
    cfg = request.app.state.config
    try:
        pdf_bytes = request.app.state.drive_client.download_pdf(
            file_id,
            log_context={
                "trace_id": trace_id,
                "request_id": request_id,
                "phase": "drive_download",
            },
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
            request_id=request_id,
            file_id=file_id,
            error_type=type(exc).__name__,
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


@router.post("/drive/poll", tags=["process"])
async def poll_drive_input_to_ingest(request: Request) -> JSONResponse:
    """Mirror Drive intake PDFs into GCS so Eventarc can invoke /ingest."""
    trace_id, request_id = _extract_correlation_ids(request)
    cfg = request.app.state.config
    limit = _resolve_drive_poll_limit()
    drive_client = request.app.state.drive_client

    try:
        candidates = drive_client.list_input_pdfs(
            cfg.drive_input_folder_id,
            drive_id=cfg.drive_shared_drive_id,
            limit=limit,
        )
    except (DriveServiceError, RuntimeError, ValueError) as exc:
        structured_log(
            _API_LOG,
            logging.ERROR,
            "drive_poll_list_failed",
            trace_id=trace_id,
            request_id=request_id,
            error_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=502, detail="Failed to list Drive input files"
        ) from exc

    mirrored: list[dict[str, str]] = []
    duplicates: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    for candidate in candidates:
        file_id_raw = candidate.get("id")
        file_name_raw = candidate.get("name")
        resource_key_raw = candidate.get("resource_key")
        file_id = file_id_raw.strip() if isinstance(file_id_raw, str) else ""
        file_name = file_name_raw.strip() if isinstance(file_name_raw, str) else None
        resource_key = (
            resource_key_raw.strip()
            if isinstance(resource_key_raw, str) and resource_key_raw.strip()
            else None
        )
        if not file_id:
            continue
        try:
            mirror_result = mirror_drive_pdf_to_intake(
                drive_client=drive_client,
                intake_bucket=cfg.intake_gcs_bucket,
                drive_file_id=file_id,
                source_folder_id=cfg.drive_input_folder_id,
                drive_shared_drive_id=cfg.drive_shared_drive_id,
                file_name=file_name,
                resource_key=resource_key,
            )
        except Exception as exc:  # pragma: no cover - defensive fail-safe
            structured_log(
                _API_LOG,
                logging.ERROR,
                "drive_poll_mirror_failed",
                trace_id=trace_id,
                request_id=request_id,
                drive_file_id=file_id,
                error_type=type(exc).__name__,
            )
            failures.append({"drive_file_id": file_id, "error": "mirror_failed"})
            continue

        result_payload = {
            "drive_file_id": mirror_result.drive_file_id,
            "object_uri": mirror_result.object_uri,
        }
        if mirror_result.created:
            mirrored.append(result_payload)
        else:
            duplicates.append(result_payload)

    structured_log(
        _API_LOG,
        logging.INFO,
        "drive_poll_complete",
        trace_id=trace_id,
        request_id=request_id,
        listed_count=len(candidates),
        mirrored_count=len(mirrored),
        duplicate_count=len(duplicates),
        failed_count=len(failures),
    )
    response_payload = {
        "listed_count": len(candidates),
        "mirrored_count": len(mirrored),
        "duplicate_count": len(duplicates),
        "failed_count": len(failures),
        "mirrored": mirrored,
        "duplicates": duplicates,
        "failures": failures,
    }
    status_code = 207 if failures else 200
    return JSONResponse(response_payload, status_code=status_code)
