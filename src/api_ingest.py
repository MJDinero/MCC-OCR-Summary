"""
Thin FastAPI surface for Eventarc-triggered ingestion of MCC OCR workflow jobs.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any, Dict, Mapping, Optional, Tuple
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from google.api_core.retry import Retry
from google.cloud import workflows_v1
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.responses import JSONResponse

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("mcc-ocr-summary.ingest")

app = FastAPI(title="MCC OCR Summary", version="1.0.0")

try:  # Optional CloudEvents dependency; Eventarc path uses it.
    from cloudevents.http import from_http as ce_from_http
except Exception:  # pragma: no cover - CloudEvents optional.
    ce_from_http = None


class IngestPayload(BaseModel):
    """Permissive CloudEvent-style payload expected from Eventarc."""

    id: Optional[str] = None
    type: Optional[str] = None
    source: Optional[str] = None
    time: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)
    model_config = ConfigDict(extra="allow")


def _env(name: str, default: Optional[str] = None) -> str:
    """Fetch required environment variables with optional default."""

    value = os.getenv(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


if hasattr(workflows_v1, "ExecutionsClient"):
    _ExecutionsClient = workflows_v1.ExecutionsClient  # type: ignore[attr-defined]
else:  # pragma: no cover - fallback for older client libraries
    from google.cloud.workflows.executions_v1 import ExecutionsClient as _ExecutionsClient

_wf_client = _ExecutionsClient()


def _truncate_event(event: Any, limit: int = 512) -> str:
    """Return a compact string representation of the event for logs."""

    try:
        serialized = json.dumps(event)
    except (TypeError, ValueError):
        serialized = repr(event)
    return serialized[:limit]


def _extract_object_info(event: Dict[str, Any], raw_event: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Derive bucket/name from CloudEvent or Pub/Sub style payloads."""

    bucket: Optional[str] = None
    name: Optional[str] = None

    for candidate in (event, raw_event):
        if not isinstance(candidate, dict):
            continue

        bucket = bucket or candidate.get("bucket")
        name = name or candidate.get("name") or candidate.get("object") or candidate.get("objectId")

        data = candidate.get("data")
        if isinstance(data, dict):
            bucket = bucket or data.get("bucket") or data.get("bucketId")
            name = name or data.get("name") or data.get("object") or data.get("objectId")

        message = candidate.get("message")
        if isinstance(message, dict):
            attributes = message.get("attributes")
            if isinstance(attributes, dict):
                bucket = bucket or attributes.get("bucket") or attributes.get("bucketId")
                name = name or attributes.get("name") or attributes.get("object") or attributes.get("objectId")

            encoded = message.get("data")
            if isinstance(encoded, str):
                padded = encoded + "=" * (-len(encoded) % 4)
                try:
                    decoded_bytes = base64.b64decode(padded)
                    decoded_json = json.loads(decoded_bytes.decode("utf-8"))
                except (ValueError, json.JSONDecodeError):
                    continue

                if isinstance(decoded_json, dict):
                    bucket = bucket or decoded_json.get("bucket") or decoded_json.get("bucketId")
                    name = name or decoded_json.get("name") or decoded_json.get("object") or decoded_json.get("objectId")

    return bucket, name


def _derive_trace_context(headers: Mapping[str, str]) -> Tuple[Optional[str], Optional[str]]:
    """Extract Cloud Trace context header and trace id variants."""

    trace_header = headers.get("x-cloud-trace-context")
    if trace_header:
        trace_id = _parse_trace_id(trace_header)
        return trace_header, trace_id

    trace_parent = headers.get("ce-traceparent")
    if trace_parent:
        parts = trace_parent.split("-")
        if len(parts) >= 4:
            trace_id_hex = parts[1].strip()
            span_hex = parts[2].strip()
            try:
                span_dec = str(int(span_hex, 16))
            except ValueError:
                span_dec = span_hex
            trace_context = f"{trace_id_hex}/{span_dec};o=1"
            return trace_context, trace_id_hex or None
        return trace_parent, _parse_trace_id(trace_parent)

    return None, None


def _parse_trace_id(value: str) -> Optional[str]:
    if not value:
        return None
    if "/" in value:
        candidate = value.split("/", 1)[0].strip()
        return candidate or None
    parts = value.split("-")
    if len(parts) >= 2:
        candidate = parts[1].strip()
        return candidate or None
    return value.strip() or None


@app.get("/healthz", response_class=JSONResponse)
async def healthz() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/healthz/", response_class=JSONResponse, include_in_schema=False)
async def healthz_slash() -> Dict[str, str]:
    return await healthz()


@app.get("/-/ready", response_class=JSONResponse)
async def ready() -> Dict[str, str]:
    _ = _env("PROJECT_ID")
    _ = _env("REGION", "us-central1")
    _ = _env("WORKFLOW_NAME", "docai-pipeline")
    return {"status": "ready"}


@app.post("/ingest", response_class=JSONResponse)
async def ingest(request: Request) -> Dict[str, Any]:
    """
    Accept JSON payloads (Eventarc CloudEvent format) and dispatch Cloud Workflows executions.
    """

    body_bytes = await request.body()
    headers = dict(request.headers)
    raw_event: Dict[str, Any] = {}

    if ce_from_http is not None:
        try:
            cloud_event = ce_from_http(headers, body_bytes)
            ce_dict = cloud_event.to_dict() if hasattr(cloud_event, "to_dict") else {}
            ce_data = ce_dict.get("data") if isinstance(ce_dict, dict) else None
            if isinstance(ce_data, (bytes, bytearray)):
                ce_data = json.loads(ce_data.decode("utf-8"))
            raw_event = dict(ce_dict) if isinstance(ce_dict, dict) else {}
            if ce_data is not None:
                raw_event["data"] = ce_data
        except Exception:
            raw_event = {}

    if not raw_event:
        try:
            raw_event = json.loads(body_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            logger.exception("Invalid CloudEvent or JSON payload: %s", exc)
            raise HTTPException(status_code=422, detail="Invalid CloudEvent or JSON payload") from exc

    try:
        payload = IngestPayload.model_validate(raw_event)
    except ValidationError as exc:
        logger.exception("Invalid event payload: %s", exc)
        raise HTTPException(status_code=422, detail="Invalid CloudEvent or JSON payload") from exc

    event = payload.model_dump(mode="python")
    bucket, name = _extract_object_info(event, raw_event)
    if not bucket or not name:
        logger.error(
            "Missing bucket/name in ingest event: %s",
            _truncate_event(raw_event),
        )
        raise HTTPException(status_code=400, detail="Missing object URI in event data")

    object_uri = f"gs://{bucket}/{name}"
    ce_id = request.headers.get("ce-id") or payload.id or str(uuid4())
    trace_context, trace_id_header = _derive_trace_context(request.headers)
    trace_id = trace_id_header or ce_id
    job_id = str(uuid4())

    intake_bucket = (
        os.getenv("INTAKE_GCS_BUCKET")
        or os.getenv("INTAKE_BUCKET")
        or bucket
    )
    output_bucket = os.getenv("OUTPUT_GCS_BUCKET") or os.getenv("OUTPUT_BUCKET")
    summary_bucket = os.getenv("SUMMARY_BUCKET") or output_bucket
    pipeline_base = os.getenv("PIPELINE_SERVICE_BASE_URL")

    argument: Dict[str, Optional[str]] = {
        "job_id": job_id,
        "trace_id": trace_id,
        "request_id": ce_id,
        "dedupe_key": ce_id,
        "object_uri": object_uri,
    }
    if trace_context:
        argument["trace_context"] = trace_context
    if intake_bucket:
        argument["intake_bucket"] = intake_bucket
    if output_bucket:
        argument["output_bucket"] = output_bucket
    if summary_bucket:
        argument["summary_bucket"] = summary_bucket
    argument["gcs_uri"] = object_uri
    if pipeline_base:
        argument["pipeline_service_base_url"] = pipeline_base

    project = _env("PROJECT_ID")
    region = _env("REGION", "us-central1")
    optional_fields = {
        "doc_ai_processor_id": os.getenv("DOC_AI_PROCESSOR_ID"),
        "doc_ai_splitter_processor_id": os.getenv("DOC_AI_SPLITTER_PROCESSOR_ID"),
        "summariser_job_name": os.getenv("SUMMARISER_JOB_NAME"),
        "pdf_job_name": os.getenv("PDF_JOB_NAME"),
        "pipeline_dlq_topic": os.getenv("PIPELINE_DLQ_TOPIC"),
        "max_shard_concurrency": os.getenv("MAX_SHARD_CONCURRENCY"),
        "internal_event_token": os.getenv("INTERNAL_EVENT_TOKEN"),
        "project_id": project,
        "region": region,
    }
    for key, value in optional_fields.items():
        argument[key] = value
    workflow_name = _env("WORKFLOW_NAME", "docai-pipeline")
    parent = f"projects/{project}/locations/{region}/workflows/{workflow_name}"

    execution = _wf_client.create_execution(
        request={
            "parent": parent,
            "execution": {"argument": json.dumps(argument)},
        },
        retry=Retry(initial=1.0, maximum=10.0, multiplier=2.0, deadline=30.0),
    )

    logger.info(
        "workflow_execution_dispatch",
        extra={
            "workflow": parent,
            "event_id": ce_id,
            "job_id": job_id,
            "object_uri": object_uri,
            "trace_id": trace_id,
        },
    )

    return {
        "ok": True,
        "execution": execution.name,
        "job_id": job_id,
        "object_uri": object_uri,
    }


__all__ = ["app", "IngestPayload"]
