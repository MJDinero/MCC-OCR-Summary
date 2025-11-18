"""Ingestion and job management routes for MCC OCR Summary."""

from __future__ import annotations

import base64
import json
import logging
import secrets
import time
import uuid
from typing import Any, Dict, Mapping, MutableMapping
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    Field,
    ValidationError as PydanticValidationError,
    field_validator,
)

from src.errors import ValidationError
from src.services.pipeline import (
    DuplicateJobError,
    PipelineJobCreate,
    PipelineStateStore,
    PipelineStatus,
    WorkflowLauncher,
    extract_trace_id,
    job_public_view,
)

router = APIRouter()
_INGEST_LOG = logging.getLogger("ingest")


class StorageObjectPayload(BaseModel):
    """Subset of GCS Object finalize payload fields we require."""

    bucket: str | None = None
    name: str | None = None
    generation: str | None = None
    metageneration: str | None = None
    size: int | None = Field(default=None, alias="size")
    md5_hash: str | None = Field(default=None, alias="md5Hash")
    content_type: str | None = Field(default=None, alias="contentType")
    metadata: dict[str, Any] | None = None

    @field_validator("size", mode="before")
    @classmethod
    def _coerce_size(cls, value: Any) -> int | None:
        if value is None or value == "":
            return None
        if isinstance(value, int):
            return value
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("size must be numeric") from exc

    model_config = {"populate_by_name": True}


class IngestRequest(BaseModel):
    """Payload accepted by the /ingest endpoint (Eventarc → Cloud Run)."""

    gcs_object: StorageObjectPayload = Field(alias="object")
    source: str | None = None
    drive_file_id: str | None = Field(default=None, alias="driveFileId")
    attributes: dict[str, Any] | None = None
    trace_id: str | None = None
    request_id: str | None = None

    model_config = {"populate_by_name": True}


class JobEventPayload(BaseModel):
    """Internal event updates issued by Cloud Workflow steps or Jobs."""

    status: PipelineStatus
    stage: str | None = None
    message: str | None = None
    extra: dict[str, Any] | None = None
    retry_stage: str | None = Field(default=None, alias="retryStage")
    pdf_uri: str | None = Field(default=None, alias="pdfUri")
    signed_url: str | None = Field(default=None, alias="signedUrl")
    lro_name: str | None = Field(default=None, alias="lroName")
    last_error: dict[str, Any] | None = Field(default=None, alias="lastError")
    metadata_patch: dict[str, Any] | None = Field(default=None, alias="metadataPatch")

    model_config = {"populate_by_name": True}


def _b64_json_decode(value: str) -> Dict[str, Any] | None:
    padded = value + "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(padded)
        return json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _first_non_empty(*candidates: Any) -> Any:
    for item in candidates:
        if isinstance(item, str) and item.strip():
            return item
        if item not in (None, "", {}):
            return item
    return None


def _extract_traceparent(trace_parent: str | None) -> tuple[str | None, str | None]:
    if not trace_parent:
        return None, None
    parts = trace_parent.split("-")
    if len(parts) < 4:
        return None, None
    trace_id_hex = parts[1].strip()
    span_hex = parts[2].strip()
    if not trace_id_hex:
        return None, None
    try:
        span_dec = str(int(span_hex, 16))
    except ValueError:
        span_dec = span_hex
    return f"{trace_id_hex}/{span_dec};o=1", trace_id_hex


def _build_ingest_payload(
    raw: Dict[str, Any], headers: Mapping[str, str]
) -> Dict[str, Any]:
    if "object" in raw:
        payload = dict(raw)
    elif "data" in raw and isinstance(raw["data"], dict):
        payload = {"object": raw["data"]}
    elif "bucket" in raw and "name" in raw:
        payload = {"object": raw}
    elif "message" in raw and isinstance(raw["message"], MutableMapping):
        message = raw["message"]
        attributes = message.get("attributes")
        if not isinstance(attributes, MutableMapping):
            attributes = {}
        decoded: Dict[str, Any] | None = None
        data_field = message.get("data")
        if isinstance(data_field, str):
            decoded = _b64_json_decode(data_field)
        elif isinstance(data_field, MutableMapping):
            decoded = dict(data_field)
        gcs_source: Dict[str, Any] = decoded if isinstance(decoded, dict) else {}
        gcs_object = dict(gcs_source)
        for key, candidates in (
            ("bucket", ("bucket", "bucketId", "bucket_id")),
            ("name", ("name", "object", "objectId", "object_id")),
            ("generation", ("generation", "objectGeneration")),
            ("metageneration", ("metageneration",)),
            ("md5Hash", ("md5Hash", "md5hash")),
            ("size", ("size",)),
            ("contentType", ("contentType", "content_type")),
        ):
            if not gcs_object.get(key):
                fallback = _first_non_empty(
                    *(gcs_source.get(c) for c in candidates),
                    *(attributes.get(c) for c in candidates),
                )
                if fallback is not None:
                    gcs_object[key] = fallback
        if "metadata" in gcs_source and isinstance(gcs_source["metadata"], dict):
            gcs_object["metadata"] = dict(gcs_source["metadata"])
        payload = {"object": gcs_object}
        if attributes:
            payload["attributes"] = dict(attributes)
        if raw.get("source"):
            payload["source"] = raw["source"]
        request_id = (
            raw.get("requestId")
            or attributes.get("eventId")
            or message.get("messageId")
        )
        if request_id:
            payload["requestId"] = request_id
    else:
        payload = {"object": raw}
    payload.setdefault(
        "traceId", headers.get("x-trace-id") or headers.get("ce-traceid")
    )
    return payload


async def _parse_ingest_request(request: Request) -> IngestRequest:
    try:
        raw = await request.json()
    except json.JSONDecodeError as exc:
        raise ValidationError("Expected JSON body") from exc

    payload_dict = _build_ingest_payload(raw, request.headers)
    try:
        return IngestRequest(**payload_dict)
    except PydanticValidationError as exc:
        raise ValidationError(f"Invalid ingest payload: {exc}") from exc


@router.post("", tags=["ingest"])
async def ingest(request: Request):
    payload = await _parse_ingest_request(request)
    state_store: PipelineStateStore = request.app.state.state_store
    workflow_launcher: WorkflowLauncher = request.app.state.workflow_launcher

    trace_header = request.headers.get("x-cloud-trace-context")
    trace_parent_ctx, trace_parent_id = _extract_traceparent(
        request.headers.get("ce-traceparent")
    )
    if not trace_header and trace_parent_ctx:
        trace_header = trace_parent_ctx
    trace_id = (
        payload.trace_id
        or extract_trace_id(trace_header)
        or trace_parent_id
        or uuid.uuid4().hex
    )
    request_id = payload.request_id or request.headers.get("ce-id") or trace_id
    payload = payload.model_copy(
        update={"trace_id": trace_id, "request_id": request_id}
    )
    gcs_obj = payload.gcs_object

    if not gcs_obj.bucket or not gcs_obj.name:
        raise ValidationError("GCS object bucket and name are required")

    combined_metadata: dict[str, Any] = {}
    if gcs_obj.metadata:
        combined_metadata.update(gcs_obj.metadata)
    if payload.attributes:
        combined_metadata.update(payload.attributes)
    cfg = request.app.state.config
    ingest_started = time.perf_counter()

    job_create = PipelineJobCreate(
        bucket=gcs_obj.bucket,
        object_name=gcs_obj.name,
        generation=gcs_obj.generation,
        metageneration=gcs_obj.metageneration,
        size_bytes=gcs_obj.size,
        md5_hash=gcs_obj.md5_hash,
        metadata=combined_metadata,
        trace_id=trace_id,
        source=payload.source,
        drive_file_id=payload.drive_file_id,
        request_id=payload.request_id,
    )

    try:
        job = state_store.create_job(job_create)
        duration_ms = int((time.perf_counter() - ingest_started) * 1000)
        log_extra = {
            "job_id": job.job_id,
            "trace_id": job.trace_id,
            "document_id": job.object_uri,
            "shard_id": "origin",
            "duration_ms": duration_ms,
            "schema_version": cfg.summary_schema_version,
            "attempt": job.retries.get("INGEST", 0) + 1,
            "component": "ingest_service",
            "severity": "INFO",
            "bucket": gcs_obj.bucket,
            "object_name": gcs_obj.name,
            "generation": gcs_obj.generation,
        }
        if job.trace_id:
            log_extra["logging.googleapis.com/trace"] = (
                f"projects/{cfg.project_id}/traces/{job.trace_id}"
            )
    except DuplicateJobError as dup:
        job = dup.job
        duration_ms = int((time.perf_counter() - ingest_started) * 1000)
        log_extra = {
            "job_id": job.job_id,
            "trace_id": job.trace_id,
            "document_id": job.object_uri,
            "shard_id": "origin",
            "duration_ms": duration_ms,
            "schema_version": cfg.summary_schema_version,
            "attempt": job.retries.get("INGEST", 0) + 1,
            "component": "ingest_service",
            "severity": "INFO",
            "bucket": gcs_obj.bucket,
            "object_name": gcs_obj.name,
            "generation": gcs_obj.generation,
        }
        log_extra["duplicate"] = True
        _INGEST_LOG.info("ingest_duplicate", extra=log_extra)
        response_payload = job_public_view(job)
        response_payload["duplicate"] = True
        return JSONResponse(response_payload, status_code=412)

    _INGEST_LOG.info("ingest_received", extra=log_extra)

    execution_name: str | None = None
    try:
        dispatcher: WorkflowLauncher | None = workflow_launcher
        if dispatcher is None:
            raise RuntimeError("Workflow launcher not configured")

        workflow_parameters = {
            "bucket": gcs_obj.bucket,
            "object": gcs_obj.name,
            "trace_id": trace_id,
            "job_id": job.job_id,
            "object_uri": job.object_uri,
            "object_name": gcs_obj.name,
            "request_id": job.request_id,
            "intake_bucket": cfg.intake_gcs_bucket,
            "output_bucket": cfg.output_gcs_bucket,
            "summary_bucket": cfg.summary_bucket,
            "summary_schema_version": cfg.summary_schema_version,
        }
        pipeline_base = os.getenv("PIPELINE_SERVICE_BASE_URL")
        if pipeline_base:
            workflow_parameters["pipeline_service_base_url"] = pipeline_base
        dlq_topic = os.getenv("PIPELINE_DLQ_TOPIC")
        if dlq_topic:
            workflow_parameters["pipeline_dlq_topic"] = dlq_topic
        summariser_job = os.getenv("SUMMARISER_JOB_NAME")
        if summariser_job:
            workflow_parameters["summariser_job_name"] = summariser_job
        pdf_job = os.getenv("PDF_JOB_NAME")
        if pdf_job:
            workflow_parameters["pdf_job_name"] = pdf_job
        internal_token = getattr(request.app.state, "internal_event_token", None)
        if internal_token:
            workflow_parameters["internal_event_token"] = internal_token
        if payload.source:
            workflow_parameters["source"] = payload.source
        if payload.drive_file_id:
            workflow_parameters["drive_file_id"] = payload.drive_file_id
        if cfg.drive_shared_drive_id:
            workflow_parameters["drive_shared_drive_id"] = cfg.drive_shared_drive_id
        if hasattr(dispatcher, "launch"):
            launch_result = dispatcher.launch(
                job=job, parameters=workflow_parameters, trace_context=trace_header
            )
        elif callable(dispatcher):
            launch_result = dispatcher(
                job=job, parameters=workflow_parameters, trace_context=trace_header
            )
        else:
            raise TypeError("workflow_launcher does not support launch(job=...)")

        if launch_result:
            execution_name = launch_result
    except Exception as exc:
        state_store.mark_status(
            job.job_id,
            PipelineStatus.FAILED,
            stage="WORKFLOW_DISPATCH",
            message=str(exc),
            extra={"error": str(exc)},
            updates={"last_error": {"stage": "workflow_dispatch", "error": str(exc)}},
        )
        _INGEST_LOG.exception(
            "workflow_dispatch_failed",
            extra={"job_id": job.job_id, "error": str(exc), "trace_id": job.trace_id},
        )
        raise HTTPException(
            status_code=502, detail="Failed to dispatch workflow"
        ) from exc

    updates = {}
    if execution_name:
        updates["workflow_execution"] = execution_name

    job = state_store.mark_status(
        job.job_id,
        PipelineStatus.WORKFLOW_DISPATCHED,
        stage="WORKFLOW",
        message="Workflow execution dispatched",
        extra={"execution": execution_name},
        updates=updates or None,
    )

    response_payload = job_public_view(job)
    response_payload["duplicate"] = False
    return JSONResponse(response_payload, status_code=202)


@router.get("/status/{job_id}", tags=["ingest"])
async def job_status(request: Request, job_id: str):
    state_store: PipelineStateStore = request.app.state.state_store
    job = state_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job_public_view(job)


def _merge_metadata(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    merged.update(patch)
    return merged


@router.post("/internal/jobs/{job_id}/events", tags=["ingest"])
async def record_job_event(request: Request, job_id: str, event: JobEventPayload):
    expected_token = request.app.state.internal_event_token
    provided = request.headers.get("x-internal-event-token", "")
    if not expected_token or not provided or not secrets.compare_digest(provided, expected_token):  # type: ignore[name-defined]
        raise HTTPException(status_code=401, detail="Missing or invalid internal token")

    state_store: PipelineStateStore = request.app.state.state_store
    job = state_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    metadata_base = dict(job.metadata)
    if event.retry_stage:
        job = state_store.record_retry(job_id, event.retry_stage)

    updates: dict[str, Any] = {}
    if event.pdf_uri is not None:
        updates["pdf_uri"] = event.pdf_uri
    if event.signed_url is not None:
        updates["signed_url"] = event.signed_url
    if event.lro_name is not None:
        updates["lro_name"] = event.lro_name
    if event.last_error is not None:
        updates["last_error"] = event.last_error
    if event.metadata_patch:
        updates["metadata"] = _merge_metadata(metadata_base, event.metadata_patch)

    try:
        job = state_store.mark_status(
            job_id,
            event.status,
            stage=event.stage,
            message=event.message,
            extra=event.extra,
            updates=updates or None,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found")
    return job_public_view(job)
