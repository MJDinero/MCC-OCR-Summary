"""FastAPI application for the asynchronous MCC OCR → Summary pipeline."""
from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import socket
import sys
import time
import uuid
from typing import Any, Dict, Mapping, MutableMapping

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError as PydanticValidationError, field_validator

from src.config import get_config
from src.errors import ValidationError
from src.logging_setup import configure_logging
from src.services.pipeline import (
    DuplicateJobError,
    PipelineJobCreate,
    PipelineStateStore,
    PipelineStatus,
    WorkflowLauncher,
    create_state_store_from_env,
    create_workflow_launcher_from_env,
    extract_trace_id,
    job_public_view,
)
from src.services.docai_helper import OCRService
from src.services.metrics import PrometheusMetrics
from src.utils.secrets import resolve_secret_env

# Force stdout logging early (before configure_logging)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
    force=True,
)
logging.info("✅ Logging initialised (stdout)")

_API_LOG = logging.getLogger("api")


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


def _health_payload() -> dict[str, str]:
    return {"status": "ok"}


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


def _build_ingest_payload(raw: Dict[str, Any], headers: Mapping[str, str]) -> Dict[str, Any]:
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
                fallback = _first_non_empty(*(gcs_source.get(c) for c in candidates), *(attributes.get(c) for c in candidates))
                if fallback is not None:
                    gcs_object[key] = fallback
        if "metadata" in gcs_source and isinstance(gcs_source["metadata"], dict):
            gcs_object["metadata"] = dict(gcs_source["metadata"])
        payload = {"object": gcs_object}
        if attributes:
            payload["attributes"] = dict(attributes)
        if raw.get("source"):
            payload["source"] = raw["source"]
        request_id = raw.get("requestId") or attributes.get("eventId") or message.get("messageId")
        if request_id:
            payload["request_id"] = request_id
        trace_id = raw.get("traceId") or attributes.get("traceId")
        if trace_id:
            payload["trace_id"] = trace_id
    else:
        payload = {}

    if "object" not in payload or not isinstance(payload["object"], MutableMapping):
        raise HTTPException(status_code=422, detail="Invalid CloudEvent or JSON payload")

    ce_id = headers.get("ce-id")
    if ce_id:
        payload["request_id"] = ce_id

    trace_parent_ctx, trace_parent_id = _extract_traceparent(headers.get("ce-traceparent"))
    if trace_parent_id and not payload.get("trace_id"):
        payload["trace_id"] = trace_parent_id
    if trace_parent_ctx and "trace_context" not in payload:
        payload["trace_context"] = trace_parent_ctx

    return payload


async def _parse_ingest_request(request: Request) -> IngestRequest:
    body_bytes = await request.body()
    if not body_bytes:
        raise HTTPException(status_code=422, detail="Invalid CloudEvent or JSON payload")
    try:
        decoded = json.loads(body_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid CloudEvent or JSON payload") from exc
    if not isinstance(decoded, dict):
        raise HTTPException(status_code=422, detail="Invalid CloudEvent or JSON payload")
    payload_dict = _build_ingest_payload(decoded, request.headers)
    try:
        return IngestRequest.model_validate(payload_dict)
    except PydanticValidationError as exc:
        raise HTTPException(status_code=422, detail="Invalid CloudEvent or JSON payload") from exc


def _require_internal_token(request: Request, *, expected: str | None) -> None:
    if not expected:
        raise HTTPException(status_code=401, detail="Missing or invalid internal token")
    provided = request.headers.get("x-internal-event-token", "")
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Missing or invalid internal token")


def _merge_metadata(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        merged[key] = value
    return merged


def create_app() -> FastAPI:
    configure_logging()
    try:  # refresh cache for tests altering env
        get_config.cache_clear()  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover
        pass

    cfg = get_config()
    app = FastAPI(title="MCC-OCR-Summary", version="1.0.0")
    app.state.config = cfg
    app.state.state_store = create_state_store_from_env()
    app.state.workflow_launcher = create_workflow_launcher_from_env()
    app.state.metrics = PrometheusMetrics.instrument_app(app)
    internal_token = resolve_secret_env("INTERNAL_EVENT_TOKEN", project_id=cfg.project_id)
    if not internal_token:
        raise RuntimeError("INTERNAL_EVENT_TOKEN must be configured via Secret Manager or environment variable")
    app.state.internal_event_token = internal_token

    @app.exception_handler(ValidationError)
    async def _val_handler(_r: Request, exc: ValidationError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    # Health endpoints ---------------------------------------------------------
    @app.get("/healthz", summary="Healthz")
    async def healthz():
        return _health_payload()

    @app.get("/health", include_in_schema=False)
    async def health_alias():
        return _health_payload()

    @app.get("/readyz", include_in_schema=False)
    async def readyz():
        return _health_payload()

    @app.get("/", include_in_schema=False)
    async def root_health():
        return _health_payload()

    # Event-driven ingestion ---------------------------------------------------
    @app.post("/ingest")
    async def ingest(request: Request):
        payload = await _parse_ingest_request(request)
        state_store: PipelineStateStore = app.state.state_store
        workflow_launcher: WorkflowLauncher = app.state.workflow_launcher

        trace_header = request.headers.get("x-cloud-trace-context")
        trace_parent_ctx, trace_parent_id = _extract_traceparent(request.headers.get("ce-traceparent"))
        if not trace_header and trace_parent_ctx:
            trace_header = trace_parent_ctx
        trace_id = payload.trace_id or extract_trace_id(trace_header) or trace_parent_id or uuid.uuid4().hex
        request_id = payload.request_id or request.headers.get("ce-id") or trace_id
        payload = payload.model_copy(update={"trace_id": trace_id, "request_id": request_id})
        gcs_obj = payload.gcs_object

        if not gcs_obj.bucket or not gcs_obj.name:
            raise ValidationError("GCS object bucket and name are required")

        combined_metadata: dict[str, Any] = {}
        if gcs_obj.metadata:
            combined_metadata.update(gcs_obj.metadata)
        if payload.attributes:
            combined_metadata.update(payload.attributes)
        cfg = get_config()
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
                log_extra["logging.googleapis.com/trace"] = f"projects/{cfg.project_id}/traces/{job.trace_id}"
            _API_LOG.info("ingest_received", extra=log_extra)
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
                "duplicate": True,
            }
            if job.trace_id:
                log_extra["logging.googleapis.com/trace"] = f"projects/{cfg.project_id}/traces/{job.trace_id}"
            _API_LOG.info("ingest_received", extra=log_extra)
            _API_LOG.info(
                "ingest_duplicate",
                extra={"job_id": job.job_id, "dedupe_key": job.dedupe_key, "trace_id": job.trace_id},
            )
            response_payload = job_public_view(job)
            response_payload["duplicate"] = True
            return JSONResponse(response_payload, status_code=412)
        except Exception as exc:  # pragma: no cover - unexpected
            _API_LOG.exception("ingest_create_failed", extra={"error": str(exc)})
            raise HTTPException(status_code=500, detail="Failed to create pipeline job") from exc

        execution_name: str | None = None

        try:
            launch_result: str | None = None
            pipeline_service_base_url = os.getenv("PIPELINE_SERVICE_BASE_URL")
            summariser_job_name = os.getenv("SUMMARISER_JOB_NAME")
            pdf_job_name = os.getenv("PDF_JOB_NAME")
            pipeline_dlq_topic = os.getenv("PIPELINE_DLQ_TOPIC") or cfg.pipeline_pubsub_topic
            doc_ai_location_env = os.getenv("DOC_AI_LOCATION")

            workflow_parameters = {
                "bucket": gcs_obj.bucket,
                "object_name": gcs_obj.name,
                "generation": gcs_obj.generation,
                "gcs_uri": job.object_uri,
                "object_uri": job.object_uri,
                "job_id": job.job_id,
                "trace_id": job.trace_id,
                "request_id": job.request_id,
                "dedupe_key": job.dedupe_key,
                "object_hash": job.object_hash,
                "md5_hash": job.md5_hash,
                "pipeline_service_base_url": pipeline_service_base_url,
                "internal_event_token": app.state.internal_event_token,
                "project_id": cfg.project_id,
                "region": cfg.region,
                "doc_ai_location": doc_ai_location_env or cfg.region,
                "doc_ai_processor_id": cfg.doc_ai_processor_id,
                "doc_ai_splitter_processor_id": cfg.doc_ai_splitter_id,
                "summariser_job_name": summariser_job_name,
                "pdf_job_name": pdf_job_name,
                "intake_bucket": cfg.intake_gcs_bucket,
                "output_bucket": cfg.output_gcs_bucket,
                "summary_bucket": cfg.summary_bucket,
                "max_shard_concurrency": cfg.max_shard_concurrency,
                "pipeline_dlq_topic": pipeline_dlq_topic,
                "summary_schema_version": cfg.summary_schema_version,
            }

            if hasattr(workflow_launcher, "launch"):
                launch_result = workflow_launcher.launch(  # type: ignore[attr-defined]  # pylint: disable=assignment-from-none
                    job=job,
                    parameters=workflow_parameters,
                    trace_context=trace_header,
                )
            elif callable(workflow_launcher):
                launch_result = workflow_launcher(  # type: ignore[call-arg]  # pylint: disable=assignment-from-none
                    job=job,
                    parameters=workflow_parameters,
                    trace_context=trace_header,
                )
            else:
                raise TypeError("workflow_launcher is not callable")

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
            _API_LOG.exception(
                "workflow_dispatch_failed", extra={"job_id": job.job_id, "error": str(exc), "trace_id": job.trace_id}
            )
            raise HTTPException(status_code=502, detail="Failed to dispatch workflow") from exc

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

    # Status lookup ------------------------------------------------------------
    @app.get("/status/{job_id}")
    async def job_status(job_id: str):
        state_store: PipelineStateStore = app.state.state_store
        job = state_store.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job_public_view(job)

    # Internal event ingress ---------------------------------------------------
    @app.post("/internal/jobs/{job_id}/events")
    async def record_job_event(job_id: str, event: JobEventPayload, request: Request):
        _require_internal_token(request, expected=app.state.internal_event_token)
        state_store: PipelineStateStore = app.state.state_store
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

    # Startup diagnostics ------------------------------------------------------
    @app.on_event("startup")
    async def _startup_diag():  # pragma: no cover
        cfg.validate_required()
        routes = [getattr(r, "path", str(r)) for r in app.router.routes]
        _API_LOG.info("boot_canary", extra={"service": "mcc-ocr-summary", "routes": routes})
        _API_LOG.info("service_startup_marker", extra={"phase": "post-config", "version": app.version})
        try:
            import openai  # type: ignore

            _API_LOG.info("openai_sdk_version", extra={"version": getattr(openai, "__version__", "unknown")})
        except Exception:
            _API_LOG.info("openai_sdk_version_unavailable")
        try:
            resolved_ip = socket.gethostbyname("api.openai.com")
            _API_LOG.info("openai_dns_resolution", extra={"host": "api.openai.com", "ip": resolved_ip})
        except Exception as err:
            _API_LOG.error("openai_dns_resolution_failed", extra={"error": str(err)})

    # Connectivity diagnostics -------------------------------------------------
    @app.get("/ping_openai")
    async def ping_openai():  # pragma: no cover - external call
        import requests

        payload: dict[str, Any] = {"ts": time.time()}
        host = "api.openai.com"
        start = time.perf_counter()
        try:
            ip = socket.gethostbyname(host)
            payload["dns_ip"] = ip
        except Exception as err:
            payload["dns_error"] = str(err)
            _API_LOG.error("ping_openai_dns_failure", extra=payload)
            return payload

        cfg_state = getattr(app.state, "config", None)
        project_id = getattr(cfg_state, "project_id", None)
        api_key_raw = resolve_secret_env("OPENAI_API_KEY", project_id=project_id) or ""
        api_key = api_key_raw.strip().replace("\n", "")
        if api_key_raw and api_key != api_key_raw:
            payload["api_key_sanitized"] = True
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            response = requests.get("https://api.openai.com/v1/models", headers=headers, timeout=15)
            elapsed = round(time.perf_counter() - start, 3)
            payload.update({"status": response.status_code, "elapsed_s": elapsed, "text_head": response.text[:120]})
            level = _API_LOG.info if 200 <= response.status_code < 300 else _API_LOG.warning
            level("ping_openai_result", extra=payload)
            return payload
        except Exception as err:  # pragma: no cover - network specific
            payload["error"] = str(err)
            _API_LOG.error("ping_openai_exception", extra=payload)
            return payload

    return app


__all__ = ["create_app", "OCRService"]
