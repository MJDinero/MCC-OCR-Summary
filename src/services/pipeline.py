"""Event-driven pipeline primitives for the MCC OCR summary system.

This module defines the shared data structures and state persistence utilities
used by the asynchronous, Google Cloud aligned pipeline. Cloud Run services,
Workflows steps, and FastAPI endpoints should interact with this layer to
record job lifecycle transitions, guarantee idempotency, and surface structured
observability metadata.

High-level responsibilities implemented here:

* `PipelineStatus` enumeration capturing canonical state transitions
  (INGESTED → SPLIT_DONE → OCR_DONE → SUMMARY_DONE → PDF_DONE → COMPLETED/FAILED).
* `PipelineJob` dataclass describing persisted job metadata and history.
* `PipelineStateStore` abstraction with in-memory (tests) and GCS-backed
  implementations. GCS storage writes use ifGenerationMatch for idempotency.
* `WorkflowLauncher` abstraction to trigger Cloud Workflows executions with
  trace propagation.
* Helper functions to create state stores and workflow launchers from the
  runtime configuration/environment.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, MutableMapping, Protocol, TypedDict

from src.utils.secrets import resolve_secret_env

try:  # pragma: no cover - optional dependency for GCS persistence
    from google.cloud import storage  # type: ignore
    from google.api_core import exceptions as gexc  # type: ignore
except Exception:  # pragma: no cover - allow tests without GCP libs
    storage = None  # type: ignore
    gexc = None  # type: ignore

try:  # pragma: no cover - optional dependency for Workflows
    from google.cloud import workflows  # type: ignore
    from google.cloud.workflows import executions_v1  # type: ignore
    from google.api_core.retry import Retry  # type: ignore
except Exception:  # pragma: no cover
    workflows = None  # type: ignore
    executions_v1 = None  # type: ignore
    Retry = None  # type: ignore

LOG = logging.getLogger("pipeline")


class PipelineStatus(str, Enum):
    """Enumeration of job states recorded in Firestore/GCS.

    The pipeline progresses left-to-right; terminal states are COMPLETED or FAILED.
    """

    INGESTED = "INGESTED"
    WORKFLOW_DISPATCHED = "WORKFLOW_DISPATCHED"
    SPLIT_SCHEDULED = "SPLIT_SCHEDULED"
    SPLIT_DONE = "SPLIT_DONE"
    OCR_SCHEDULED = "OCR_SCHEDULED"
    OCR_DONE = "OCR_DONE"
    SUMMARY_SCHEDULED = "SUMMARY_SCHEDULED"
    SUMMARY_DONE = "SUMMARY_DONE"
    SUPERVISOR_SCHEDULED = "SUPERVISOR_SCHEDULED"
    SUPERVISOR_DONE = "SUPERVISOR_DONE"
    PDF_SCHEDULED = "PDF_SCHEDULED"
    PDF_DONE = "PDF_DONE"
    OUTPUT_READY = "OUTPUT_READY"
    UPLOADED = "UPLOADED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class JobHistoryEntry(TypedDict, total=False):
    status: str
    stage: str
    message: str
    timestamp: float
    extra: Dict[str, Any]


@dataclass(slots=True)
class PipelineJob:
    job_id: str
    dedupe_key: str
    object_uri: str
    bucket: str
    object_name: str
    generation: str
    metageneration: str | None
    size_bytes: int | None
    md5_hash: str | None
    trace_id: str
    request_id: str
    object_hash: str | None = None
    workflow_execution: str | None = None
    status: PipelineStatus = PipelineStatus.INGESTED
    metadata: Dict[str, Any] = field(default_factory=dict)
    history: list[JobHistoryEntry] = field(default_factory=list)
    retries: Dict[str, int] = field(default_factory=dict)
    lro_name: str | None = None
    pdf_uri: str | None = None
    signed_url: str | None = None
    last_error: Dict[str, Any] | None = None
    created_at: float = field(default_factory=lambda: time.time())
    updated_at: float = field(default_factory=lambda: time.time())


@dataclass(slots=True)
class PipelineJobCreate:
    bucket: str
    object_name: str
    generation: str | None
    metageneration: str | None = None
    size_bytes: int | None = None
    md5_hash: str | None = None
    metadata: Dict[str, Any] | None = None
    trace_id: str | None = None
    source: str | None = None
    drive_file_id: str | None = None
    request_id: str | None = None


class DuplicateJobError(RuntimeError):
    """Raised when a dedupe key already exists in the state store."""

    def __init__(self, existing_job: PipelineJob):
        super().__init__(f"Pipeline job already exists for {existing_job.dedupe_key}")
        self.job = existing_job


class PipelineStateStore(Protocol):
    """Abstract persistence interface for pipeline job state."""

    def create_job(self, payload: PipelineJobCreate) -> PipelineJob:
        ...

    def get_job(self, job_id: str) -> PipelineJob | None:
        ...

    def get_by_dedupe(self, dedupe_key: str) -> PipelineJob | None:
        ...

    def mark_status(
        self,
        job_id: str,
        status: PipelineStatus,
        *,
        stage: str | None = None,
        message: str | None = None,
        extra: Dict[str, Any] | None = None,
        updates: Dict[str, Any] | None = None,
    ) -> PipelineJob:
        ...

    def record_retry(self, job_id: str, stage: str) -> PipelineJob:
        ...

    def update_fields(self, job_id: str, **fields: Any) -> PipelineJob:
        ...


def _now_ts() -> float:
    return time.time()


def _generate_job_id() -> str:
    return uuid.uuid4().hex


def build_object_uri(bucket: str, object_name: str) -> str:
    object_name = object_name.lstrip("/")
    return f"gs://{bucket}/{object_name}"


def _normalise_hash_component(value: str | None, seed: str) -> str:
    """Normalise optional hash inputs into a deterministic lowercase hex digest."""

    if value:
        candidate = value.strip()
        if candidate:
            try:
                raw = base64.b64decode(candidate, validate=True)
                if raw:
                    return raw.hex()[:32]
            except (binascii.Error, ValueError):
                pass
            cleaned = "".join(ch for ch in candidate.lower() if ch.isalnum())
            if cleaned:
                return cleaned[:32]
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def build_dedupe_key(bucket: str, object_name: str, generation: str | None, object_hash: str | None = None) -> str:
    safe_generation = generation or "nogeneration"
    seed = f"{bucket}/{object_name}@{safe_generation}"
    if object_hash:
        cleaned = "".join(ch for ch in object_hash.lower() if ch.isalnum())
        hash_component = cleaned[:32] if cleaned else _normalise_hash_component(None, seed)
    else:
        hash_component = _normalise_hash_component(None, seed)
    return f"{seed}#{hash_component}"


def _initial_history_entry(status: PipelineStatus) -> JobHistoryEntry:
    return JobHistoryEntry(status=status.value, timestamp=_now_ts())


class InMemoryStateStore(PipelineStateStore):
    """Thread-safe in-memory store used for tests and local development."""

    def __init__(self) -> None:
        self._jobs: Dict[str, PipelineJob] = {}
        self._by_dedupe: Dict[str, str] = {}
        self._lock = threading.RLock()

    def create_job(self, payload: PipelineJobCreate) -> PipelineJob:
        with self._lock:
            safe_generation = payload.generation or "nogeneration"
            seed = f"{payload.bucket}/{payload.object_name}@{safe_generation}"
            object_hash = _normalise_hash_component(payload.md5_hash, seed)
            dedupe_key = build_dedupe_key(payload.bucket, payload.object_name, payload.generation, object_hash)
            if dedupe_key in self._by_dedupe:
                existing = self._jobs[self._by_dedupe[dedupe_key]]
                raise DuplicateJobError(existing)
            job = PipelineJob(
                job_id=_generate_job_id(),
                dedupe_key=dedupe_key,
                object_uri=build_object_uri(payload.bucket, payload.object_name),
                bucket=payload.bucket,
                object_name=payload.object_name,
                generation=safe_generation,
                metageneration=payload.metageneration,
                size_bytes=payload.size_bytes,
                md5_hash=payload.md5_hash,
                object_hash=object_hash,
                trace_id=payload.trace_id or uuid.uuid4().hex,
                request_id=payload.request_id or uuid.uuid4().hex,
                metadata={
                    "source": payload.source,
                    "drive_file_id": payload.drive_file_id,
                    **(payload.metadata or {}),
                },
                history=[_initial_history_entry(PipelineStatus.INGESTED)],
            )
            self._jobs[job.job_id] = job
            self._by_dedupe[dedupe_key] = job.job_id
            LOG.info(
                "pipeline_job_created",
                extra={
                    "job_id": job.job_id,
                    "dedupe_key": dedupe_key,
                    "object_uri": job.object_uri,
                    "trace_id": job.trace_id,
                },
            )
            return job

    def get_job(self, job_id: str) -> PipelineJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return None if job is None else _clone_job(job)

    def get_by_dedupe(self, dedupe_key: str) -> PipelineJob | None:
        with self._lock:
            job_id = self._by_dedupe.get(dedupe_key)
            if not job_id and "#" not in dedupe_key and "@" in dedupe_key:
                bucket_obj, _, generation = dedupe_key.partition("@")
                bucket, _, object_name = bucket_obj.partition("/")
                if bucket and object_name:
                    fallback_key = build_dedupe_key(bucket, object_name, generation or None, None)
                    job_id = self._by_dedupe.get(fallback_key)
            if not job_id:
                return None
            return _clone_job(self._jobs[job_id])

    def mark_status(
        self,
        job_id: str,
        status: PipelineStatus,
        *,
        stage: str | None = None,
        message: str | None = None,
        extra: Dict[str, Any] | None = None,
        updates: Dict[str, Any] | None = None,
    ) -> PipelineJob:
        with self._lock:
            job = self._jobs[job_id]
            job.status = status
            job.updated_at = _now_ts()
            entry: JobHistoryEntry = JobHistoryEntry(
                status=status.value,
                timestamp=job.updated_at,
            )
            if stage:
                entry["stage"] = stage
            if message:
                entry["message"] = message
            if extra:
                entry["extra"] = extra
            job.history.append(entry)
            if updates:
                for key, value in updates.items():
                    setattr(job, key, value)
            log_extra: Dict[str, Any] = {
                "job_id": job.job_id,
                "status": status.value,
                "trace_id": job.trace_id,
            }
            if stage is not None:
                log_extra["stage"] = stage
            if message is not None:
                log_extra["status_message"] = message
            if extra:
                # avoid mutating caller supplied dict
                log_extra.update({k: v for k, v in extra.items() if k not in {"message", "asctime"}})
            LOG.info("pipeline_status_transition", extra=log_extra)
            return _clone_job(job)

    def record_retry(self, job_id: str, stage: str) -> PipelineJob:
        with self._lock:
            job = self._jobs[job_id]
            retries = job.retries.get(stage, 0) + 1
            job.retries[stage] = retries
            job.updated_at = _now_ts()
            LOG.warning(
                "pipeline_stage_retry",
                extra={"job_id": job.job_id, "stage": stage, "attempt": retries, "trace_id": job.trace_id},
            )
            return _clone_job(job)

    def update_fields(self, job_id: str, **fields: Any) -> PipelineJob:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in fields.items():
                setattr(job, key, value)
            job.updated_at = _now_ts()
            return _clone_job(job)


def _clone_job(job: PipelineJob) -> PipelineJob:
    cloned = PipelineJob(
        job_id=job.job_id,
        dedupe_key=job.dedupe_key,
        object_uri=job.object_uri,
        bucket=job.bucket,
        object_name=job.object_name,
        generation=job.generation,
        metageneration=job.metageneration,
        size_bytes=job.size_bytes,
        md5_hash=job.md5_hash,
        object_hash=job.object_hash,
        trace_id=job.trace_id,
        request_id=job.request_id,
        workflow_execution=job.workflow_execution,
        status=job.status,
        metadata=dict(job.metadata),
        history=list(job.history),
        retries=dict(job.retries),
        lro_name=job.lro_name,
        pdf_uri=job.pdf_uri,
        signed_url=job.signed_url,
        last_error=None if job.last_error is None else dict(job.last_error),
        created_at=job.created_at,
        updated_at=job.updated_at,
    )
    return cloned


class GCSStateStore(PipelineStateStore):  # pragma: no cover - exercised via integration
    """GCS-backed state store with strong consistency and idempotent writes."""

    def __init__(
        self,
        bucket: str,
        prefix: str = "pipeline-state",
        *,
        client: Any | None = None,
        kms_key_name: str | None = None,
    ) -> None:
        if storage is None:
            raise RuntimeError("google-cloud-storage is required for GCSStateStore")
        self._client = client or storage.Client()
        self._bucket = self._client.bucket(bucket)
        self._prefix = prefix.rstrip("/")
        self._kms_key = kms_key_name

    def create_job(self, payload: PipelineJobCreate) -> PipelineJob:
        safe_generation = payload.generation or "nogeneration"
        seed = f"{payload.bucket}/{payload.object_name}@{safe_generation}"
        object_hash = _normalise_hash_component(payload.md5_hash, seed)
        dedupe_key = build_dedupe_key(payload.bucket, payload.object_name, payload.generation, object_hash)
        object_uri = build_object_uri(payload.bucket, payload.object_name)
        job = PipelineJob(
            job_id=_generate_job_id(),
            dedupe_key=dedupe_key,
            object_uri=object_uri,
            bucket=payload.bucket,
            object_name=payload.object_name,
            generation=safe_generation,
            metageneration=payload.metageneration,
            size_bytes=payload.size_bytes,
            md5_hash=payload.md5_hash,
            object_hash=object_hash,
            trace_id=payload.trace_id or uuid.uuid4().hex,
            request_id=payload.request_id or uuid.uuid4().hex,
            metadata={
                "source": payload.source,
                "drive_file_id": payload.drive_file_id,
                **(payload.metadata or {}),
            },
            history=[_initial_history_entry(PipelineStatus.INGESTED)],
        )
        dedupe_blob = self._dedupe_blob(dedupe_key)
        body = json.dumps({"job_id": job.job_id}).encode("utf-8")
        try:
            dedupe_blob.upload_from_string(
                body,
                content_type="application/json",
                if_generation_match=0,
            )
        except gexc.PreconditionFailed as exc:  # type: ignore[attr-defined]
            existing = self.get_by_dedupe(dedupe_key)
            if existing:
                raise DuplicateJobError(existing) from exc
            raise

        self._write_job(job, if_generation_match=0)
        LOG.info(
            "pipeline_job_created",
            extra={"job_id": job.job_id, "dedupe_key": dedupe_key, "object_uri": object_uri, "trace_id": job.trace_id},
        )
        return job

    def get_job(self, job_id: str) -> PipelineJob | None:
        blob = self._job_blob(job_id)
        if not blob.exists():
            return None
        data = blob.download_as_bytes()
        payload = json.loads(data.decode("utf-8"))
        return _job_from_dict(payload)

    def get_by_dedupe(self, dedupe_key: str) -> PipelineJob | None:
        blob = self._dedupe_blob(dedupe_key)
        if not blob.exists():
            if "#" not in dedupe_key and "@" in dedupe_key:
                bucket_obj, _, generation = dedupe_key.partition("@")
                bucket, _, object_name = bucket_obj.partition("/")
                if bucket and object_name:
                    fallback_key = build_dedupe_key(bucket, object_name, generation or None, None)
                    blob = self._dedupe_blob(fallback_key)
                    if not blob.exists():
                        return None
                else:
                    return None
            else:
                return None
        data = blob.download_as_bytes()
        job_id = json.loads(data.decode("utf-8")).get("job_id")
        if not job_id:
            return None
        return self.get_job(job_id)

    def mark_status(
        self,
        job_id: str,
        status: PipelineStatus,
        *,
        stage: str | None = None,
        message: str | None = None,
        extra: Dict[str, Any] | None = None,
        updates: Dict[str, Any] | None = None,
    ) -> PipelineJob:
        for attempt in range(5):
            blob = self._job_blob(job_id)
            blob.reload()
            current_generation = blob.generation
            payload = json.loads(blob.download_as_bytes().decode("utf-8"))
            job = _job_from_dict(payload)
            job.status = status
            job.updated_at = _now_ts()
            entry: JobHistoryEntry = JobHistoryEntry(
                status=status.value,
                timestamp=job.updated_at,
            )
            if stage:
                entry["stage"] = stage
            if message:
                entry["message"] = message
            if extra:
                entry["extra"] = extra
            job.history.append(entry)
            if updates:
                for key, value in updates.items():
                    setattr(job, key, value)
            try:
                self._write_job(job, if_generation_match=current_generation)
                log_extra: Dict[str, Any] = {
                    "job_id": job.job_id,
                    "status": status.value,
                    "trace_id": job.trace_id,
                }
                if stage is not None:
                    log_extra["stage"] = stage
                if message is not None:
                    log_extra["status_message"] = message
                if extra:
                    log_extra.update({k: v for k, v in extra.items() if k not in {"message", "asctime"}})
                LOG.info("pipeline_status_transition", extra=log_extra)
                return job
            except gexc.PreconditionFailed:  # type: ignore[attr-defined]
                time.sleep(0.1 * (attempt + 1))
        raise RuntimeError("Failed to update pipeline job after multiple retries")

    def record_retry(self, job_id: str, stage: str) -> PipelineJob:
        for attempt in range(5):
            blob = self._job_blob(job_id)
            blob.reload()
            current_generation = blob.generation
            payload = json.loads(blob.download_as_bytes().decode("utf-8"))
            job = _job_from_dict(payload)
            job.retries[stage] = job.retries.get(stage, 0) + 1
            job.updated_at = _now_ts()
            try:
                self._write_job(job, if_generation_match=current_generation)
                LOG.warning(
                    "pipeline_stage_retry",
                    extra={
                        "job_id": job.job_id,
                        "stage": stage,
                        "attempt": job.retries[stage],
                        "trace_id": job.trace_id,
                    },
                )
                return job
            except gexc.PreconditionFailed:  # type: ignore[attr-defined]
                time.sleep(0.1 * (attempt + 1))
        raise RuntimeError("Failed to record retry after multiple attempts")

    def update_fields(self, job_id: str, **fields: Any) -> PipelineJob:
        for attempt in range(5):
            blob = self._job_blob(job_id)
            blob.reload()
            current_generation = blob.generation
            payload = json.loads(blob.download_as_bytes().decode("utf-8"))
            job = _job_from_dict(payload)
            for key, value in fields.items():
                setattr(job, key, value)
            job.updated_at = _now_ts()
            try:
                self._write_job(job, if_generation_match=current_generation)
                return job
            except gexc.PreconditionFailed:  # type: ignore[attr-defined]
                time.sleep(0.1 * (attempt + 1))
        raise RuntimeError("Failed to update job after multiple attempts")

    def _job_blob(self, job_id: str):
        path = f"{self._prefix}/jobs/{job_id}.json"
        blob = self._bucket.blob(path)
        if self._kms_key:
            setattr(blob, "kms_key_name", self._kms_key)
        return blob

    def _dedupe_blob(self, dedupe_key: str):
        encoded = base64.urlsafe_b64encode(dedupe_key.encode("utf-8")).decode("ascii").rstrip("=")
        path = f"{self._prefix}/dedupe/{encoded}.json"
        blob = self._bucket.blob(path)
        if self._kms_key:
            setattr(blob, "kms_key_name", self._kms_key)
        return blob

    def _write_job(self, job: PipelineJob, *, if_generation_match: int | None) -> None:
        blob = self._job_blob(job.job_id)
        payload = json.dumps(pipeline_job_to_dict(job), separators=(",", ":"), sort_keys=True)
        kwargs: Dict[str, Any] = {"content_type": "application/json"}
        if if_generation_match is not None:
            kwargs["if_generation_match"] = if_generation_match
        blob.upload_from_string(payload, **kwargs)


def _job_from_dict(payload: MutableMapping[str, Any]) -> PipelineJob:
    return PipelineJob(
        job_id=payload["job_id"],
        dedupe_key=payload["dedupe_key"],
        object_uri=payload["object_uri"],
        bucket=payload["bucket"],
        object_name=payload["object_name"],
        generation=payload["generation"],
        metageneration=payload.get("metageneration"),
        size_bytes=payload.get("size_bytes"),
        md5_hash=payload.get("md5_hash"),
        object_hash=payload.get("object_hash"),
        trace_id=payload.get("trace_id", uuid.uuid4().hex),
        request_id=payload.get("request_id", uuid.uuid4().hex),
        workflow_execution=payload.get("workflow_execution"),
        status=PipelineStatus(payload.get("status", PipelineStatus.INGESTED.value)),
        metadata=dict(payload.get("metadata") or {}),
        history=list(payload.get("history") or []),
        retries=dict(payload.get("retries") or {}),
        lro_name=payload.get("lro_name"),
        pdf_uri=payload.get("pdf_uri"),
        signed_url=payload.get("signed_url"),
        last_error=payload.get("last_error"),
        created_at=float(payload.get("created_at", _now_ts())),
        updated_at=float(payload.get("updated_at", _now_ts())),
    )


def pipeline_job_to_dict(job: PipelineJob) -> Dict[str, Any]:
    data = asdict(job)
    data["status"] = job.status.value
    return data


class WorkflowLauncher(Protocol):
    """Abstraction for launching Cloud Workflows executions."""

    def launch(
        self,
        *,
        job: PipelineJob,
        parameters: Dict[str, Any] | None = None,
        trace_context: str | None = None,
    ) -> str | None:
        ...


class NoopWorkflowLauncher(WorkflowLauncher):
    """Fallback launcher for local development/testing."""

    def launch(
        self,
        *,
        job: PipelineJob,
        parameters: Dict[str, Any] | None = None,
        trace_context: str | None = None,
    ) -> str | None:
        LOG.info(
            "workflow_launch_noop",
            extra={
                "job_id": job.job_id,
                "trace_id": job.trace_id,
                "parameters": parameters or {},
                "trace_context": trace_context,
            },
        )
        return None


class CloudWorkflowsLauncher(WorkflowLauncher):  # pragma: no cover - depends on GCP services
    """Triggers executions of a configured Cloud Workflow with trace propagation."""

    def __init__(
        self,
        *,
        workflow_name: str,
        client: Any | None = None,
    ) -> None:
        if executions_v1 is None or workflows is None:
            raise RuntimeError("google-cloud-workflows is required for CloudWorkflowsLauncher")
        self._workflow_name = workflow_name
        self._client = client or executions_v1.ExecutionsClient()

    def launch(
        self,
        *,
        job: PipelineJob,
        parameters: Dict[str, Any] | None = None,
        trace_context: str | None = None,
    ) -> str | None:
        payload = {
            "job_id": job.job_id,
            "object_uri": job.object_uri,
            "dedupe_key": job.dedupe_key,
            "trace_id": job.trace_id,
            "request_id": job.request_id,
             "object_hash": job.object_hash,
            "metadata": job.metadata,
        }
        if trace_context:
            payload["trace_context"] = trace_context
        if parameters:
            payload.update(parameters)

        execution = executions_v1.Execution(argument=json.dumps(payload))
        request = executions_v1.CreateExecutionRequest(parent=self._workflow_name, execution=execution)

        call_kwargs: Dict[str, Any] = {}
        if trace_context:
            call_kwargs.setdefault("metadata", []).append(("x-cloud-trace-context", trace_context))
        if Retry is not None:
            call_kwargs["retry"] = Retry(initial=1.0, maximum=8.0, multiplier=2.0, deadline=30.0)

        response = self._client.create_execution(request=request, **call_kwargs)
        execution_name = getattr(response, "name", None)
        LOG.info(
            "workflow_execution_created",
            extra={"job_id": job.job_id, "workflow_execution": execution_name, "trace_id": job.trace_id},
        )
        return execution_name


def create_state_store_from_env() -> PipelineStateStore:
    """Instantiate an appropriate state store based on configuration."""

    backend = os.getenv("PIPELINE_STATE_BACKEND", "memory").lower()
    if backend == "gcs":
        bucket = os.getenv("PIPELINE_STATE_BUCKET")
        if not bucket:
            raise RuntimeError("PIPELINE_STATE_BUCKET required when PIPELINE_STATE_BACKEND=gcs")
        prefix = os.getenv("PIPELINE_STATE_PREFIX", "pipeline-state")
        project_id = os.getenv("PROJECT_ID")
        kms_key = resolve_secret_env("PIPELINE_STATE_KMS_KEY", project_id=project_id)
        if not kms_key:
            kms_key = resolve_secret_env("CMEK_KEY_NAME", project_id=project_id)
        print(f"STATE_STORE_BACKEND=gcs bucket={bucket} prefix={prefix}", flush=True)
        return GCSStateStore(bucket=bucket, prefix=prefix, kms_key_name=kms_key)
    print("STATE_STORE_BACKEND=memory", flush=True)
    return InMemoryStateStore()


def create_workflow_launcher_from_env() -> WorkflowLauncher:
    """Instantiate a workflow launcher suitable for the current environment."""

    workflow_name = os.getenv("PIPELINE_WORKFLOW_NAME")
    if not workflow_name:
        return NoopWorkflowLauncher()
    return CloudWorkflowsLauncher(workflow_name=workflow_name)


def extract_trace_id(trace_context: str | None) -> str | None:
    """Extract trace id from X-Cloud-Trace-Context header values."""

    if not trace_context:
        return None
    parts = trace_context.split("/")
    if not parts:
        return None
    trace_id = parts[0].strip()
    return trace_id or None


def job_public_view(job: PipelineJob) -> Dict[str, Any]:
    """Shape job data for public API responses."""

    return {
        "job_id": job.job_id,
        "dedupe_key": job.dedupe_key,
        "status": job.status.value,
        "object_uri": job.object_uri,
        "trace_id": job.trace_id,
        "request_id": job.request_id,
        "object_hash": job.object_hash,
        "workflow_execution": job.workflow_execution,
        "pdf_uri": job.pdf_uri,
        "signed_url": job.signed_url,
        "history": list(job.history),
        "retries": dict(job.retries),
        "metadata": dict(job.metadata),
        "last_error": None if job.last_error is None else dict(job.last_error),
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


__all__ = [
    "PipelineStatus",
    "PipelineJob",
    "PipelineJobCreate",
    "PipelineStateStore",
    "DuplicateJobError",
    "InMemoryStateStore",
    "GCSStateStore",
    "WorkflowLauncher",
    "NoopWorkflowLauncher",
    "CloudWorkflowsLauncher",
    "create_state_store_from_env",
    "create_workflow_launcher_from_env",
    "build_dedupe_key",
    "build_object_uri",
    "pipeline_job_to_dict",
    "job_public_view",
    "extract_trace_id",
]
