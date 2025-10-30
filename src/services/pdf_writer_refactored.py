"""High-reliability PDF writer with deterministic output and defensive checks."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.config import get_config
from src.services.pipeline import (
    PipelineStateStore,
    PipelineStatus,
    create_state_store_from_env,
)

from src.errors import PDFGenerationError
from src.services.pdf_writer import (
    MinimalPDFBackend,
    PDFBackend,
    _wrap_text as _legacy_wrap_text,  # reuse the proven text wrapper
)

_LOG = logging.getLogger("pdf_writer.refactored")


def _wrap_text(block: str, width: int) -> List[str]:
    """Delegate to legacy wrapper while guarding against extremely long blocks."""
    sanitized = (block or "").replace("\r", " ").strip()
    if not sanitized:
        return [""]
    lines: List[str] = []
    for chunk in sanitized.split("\n"):
        chunk = chunk.strip()
        if not chunk:
            lines.append("")
            continue
        lines.extend(_legacy_wrap_text(chunk, width))
    return lines or [""]


def _normalise_summary(
    summary: Dict[str, str] | str,
) -> Tuple[List[Tuple[str, str]], Dict[str, List[str]]]:
    """Build ordered sections and capture structured index payloads."""
    if isinstance(summary, str):
        body = summary.strip()
        if not body:
            raise PDFGenerationError("Summary text empty")
        return [("Summary", body)], {}
    if not summary:
        raise PDFGenerationError("Summary structure empty")

    order = [
        "Patient Information",
        "Medical Summary",
        "Billing Highlights",
        "Legal / Notes",
    ]
    sections: List[Tuple[str, str]] = []
    for key in order:
        if key in summary:
            value = (summary.get(key) or "").strip() or "N/A"
            sections.append((key, value))
    existing_keys = {title for title, _ in sections}
    for key in sorted(
        k for k in summary.keys() if k not in existing_keys and not k.startswith("_")
    ):
        value = (summary.get(key) or "").strip()
        if value:
            sections.append((key, value))

    diag_list = [
        line.strip()
        for line in (summary.get("_diagnoses_list", "") or "").splitlines()
        if line.strip()
    ]
    prov_list = [
        line.strip()
        for line in (summary.get("_providers_list", "") or "").splitlines()
        if line.strip()
    ]
    med_list = [
        line.strip()
        for line in (summary.get("_medications_list", "") or "").splitlines()
        if line.strip()
    ]
    indices = {
        "Diagnoses": diag_list,
        "Providers": prov_list,
        "Medications / Prescriptions": med_list,
    }

    if any(indices.values()):
        sections.append(("Structured Indices", "=" * 48))
        for heading, items in indices.items():
            content = "N/A" if not items else "\n".join(f"• {item}" for item in items)
            sections.append((heading, content))
    return sections, indices


def _ensure_bytes(payload: bytes | bytearray | memoryview | BytesIO) -> bytes:
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, bytearray):
        return bytes(payload)
    if isinstance(payload, memoryview):
        return payload.tobytes()
    if isinstance(payload, BytesIO):
        payload.seek(0)
        return payload.read()
    raise PDFGenerationError(f"Unsupported PDF payload type: {type(payload)!r}")


def _load_summary(path: Path | str) -> Dict[str, Any]:
    raw_path = str(path)
    if raw_path.startswith("gs:/") and not raw_path.startswith("gs://"):
        raw_path = raw_path.replace("gs:/", "gs://", 1)
    if raw_path.startswith("gs://"):
        return _load_summary_from_gcs(raw_path)
    summary_path = Path(raw_path)
    if not summary_path.exists():
        raise PDFGenerationError(f"Summary payload not found: {summary_path}")
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise PDFGenerationError("Summary payload must be a JSON object.")
    return data


def _write_pdf(path: Path, pdf_bytes: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf_bytes)


def _parse_gcs_uri(gcs_uri: str) -> Tuple[str, str]:
    if not gcs_uri.startswith("gs://"):
        raise PDFGenerationError("GCS URI must start with gs://")
    without_scheme = gcs_uri[5:]
    bucket, _, blob_name = without_scheme.partition("/")
    if not bucket or not blob_name:
        raise PDFGenerationError("Invalid GCS URI; expected gs://bucket/object")
    return bucket, blob_name


def _load_summary_from_gcs(gcs_uri: str) -> Dict[str, Any]:
    try:
        from google.cloud import storage  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise PDFGenerationError(f"google-cloud-storage unavailable: {exc}") from exc

    bucket_name, object_name = _parse_gcs_uri(gcs_uri)
    client = storage.Client()
    blob = client.bucket(bucket_name).blob(object_name)
    try:
        payload_bytes = blob.download_as_bytes()
    except Exception as exc:  # pragma: no cover - surfaces storage errors
        raise PDFGenerationError(
            f"Failed to download summary from {gcs_uri}: {exc}"
        ) from exc
    try:
        data = json.loads(payload_bytes.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise PDFGenerationError(f"Invalid JSON summary at {gcs_uri}: {exc}") from exc
    if not isinstance(data, dict):
        raise PDFGenerationError("Summary payload must be a JSON object.")
    return data


def _upload_pdf_to_gcs(  # pragma: no cover - exercised via integration environment with real GCS
    pdf_bytes: bytes,
    gcs_uri: str,
    *,
    if_generation_match: Optional[int] = None,
):
    try:
        from google.cloud import storage  # type: ignore
        from google.api_core import exceptions as gexc  # type: ignore
        from google.cloud import exceptions as storage_exceptions  # type: ignore
        from google.resumable_media.common import InvalidResponse  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise PDFGenerationError(f"google-cloud-storage unavailable: {exc}") from exc

    bucket_name, object_name = _parse_gcs_uri(gcs_uri)
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    upload_kwargs: Dict[str, Any] = {"content_type": "application/pdf"}
    if if_generation_match is not None:
        upload_kwargs["if_generation_match"] = if_generation_match
    try:
        blob.upload_from_string(pdf_bytes, **upload_kwargs)
    except Exception as exc:
        is_precondition = False
        if isinstance(
            exc, (gexc.PreconditionFailed, storage_exceptions.PreconditionFailed)
        ):
            is_precondition = True
        elif isinstance(exc, InvalidResponse):
            status = getattr(exc, "response", None)
            status_code = getattr(status, "status_code", None)
            is_precondition = status_code == 412
        if if_generation_match == 0 and is_precondition:
            _LOG.info(
                "pdf_upload_precondition_skipped",
                extra={
                    "gcs_uri": f"gs://{bucket_name}/{object_name}",
                    "reason": str(exc),
                },
            )
            blob.reload()
            return blob
        raise
    _LOG.info(
        "pdf_uploaded_gcs",
        extra={"gcs_uri": f"gs://{bucket_name}/{object_name}", "bytes": len(pdf_bytes)},
    )
    return blob


def _kms_signing_function(
    resource_name: str,
):  # pragma: no cover - requires KMS service
    try:
        from google.cloud import kms_v1  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise PDFGenerationError(f"google-cloud-kms unavailable: {exc}") from exc
    import hashlib

    client = kms_v1.KeyManagementServiceClient()

    def _signer(message: bytes) -> bytes:
        digest = hashlib.sha256(message).digest()
        response = client.asymmetric_sign(
            request={
                "name": resource_name,
                "digest": {"sha256": digest},
            }
        )
        return response.signature

    return _signer


def _generate_signed_url(
    blob, ttl_seconds: int, *, kms_key: Optional[str] = None
) -> str:  # pragma: no cover - network call
    if ttl_seconds <= 0:
        raise PDFGenerationError("Signed URL TTL must be greater than zero.")
    expiration = timedelta(seconds=ttl_seconds)
    if kms_key:
        signer = _kms_signing_function(kms_key)
        service_account = os.getenv("SIGNED_URL_SERVICE_ACCOUNT") or os.getenv(
            "SERVICE_ACCOUNT_EMAIL"
        )
        if not service_account:
            raise PDFGenerationError(
                "SIGNED_URL_SERVICE_ACCOUNT (or SERVICE_ACCOUNT_EMAIL) must be set to use KMS signing."
            )
        url = blob.generate_signed_url(
            expiration=expiration,
            method="GET",
            version="v4",
            service_account_email=service_account,
            signing_function=signer,
        )
    else:
        url = blob.generate_signed_url(
            expiration=expiration, method="GET", version="v4"
        )
    _LOG.info(
        "pdf_signed_url_generated",
        extra={"gcs_uri": f"gs://{blob.bucket.name}/{blob.name}", "ttl": ttl_seconds},
    )
    return url


def _merge_dicts(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    merged.update(patch)
    return merged


@dataclass
class PDFWriterRefactored:
    """Wrapper around MinimalPDFBackend that enforces PDF invariants and logging."""

    backend: PDFBackend = field(default_factory=MinimalPDFBackend)
    title: str = "Document Summary"
    wrap_width: int = 100

    def build(
        self,
        summary: Dict[str, str] | str,
        *,
        log_context: Optional[Dict[str, Any]] = None,
    ) -> bytes:
        sections, indices = _normalise_summary(summary)
        formatted_sections: List[Tuple[str, str]] = []
        for heading, body in sections:
            if heading == "Structured Indices" and body == "=" * 48:
                formatted_sections.append((heading, body))
                continue
            wrapped = "\n".join(_wrap_text(body, self.wrap_width))
            formatted_sections.append((heading, wrapped))

        context = dict(log_context or {})
        context.setdefault("component", "pdf_writer")
        context.setdefault("severity", "INFO")
        context.setdefault(
            "schema_version", os.getenv("SUMMARY_SCHEMA_VERSION", "2025-10-01")
        )
        context.setdefault("shard_id", "aggregate")
        context.setdefault("attempt", 1)
        started_at = time.perf_counter()
        _LOG.info(
            "pdf_writer_started",
            extra={
                **context,
                "duration_ms": 0,
                "sections": len(formatted_sections),
                "has_indices": {k: bool(v) for k, v in indices.items()},
            },
        )
        try:
            raw_pdf = self.backend.build(self.title, formatted_sections)
        except PDFGenerationError:
            _LOG.exception("pdf_writer_backend_error")
            raise
        except Exception as exc:
            _LOG.exception("pdf_writer_unexpected_error")
            raise PDFGenerationError(f"Failed generating PDF: {exc}") from exc

        pdf_bytes = _ensure_bytes(raw_pdf)
        if not pdf_bytes.startswith(b"%PDF-"):
            raise PDFGenerationError("Generated PDF missing %PDF- header")
        if not pdf_bytes.rstrip().endswith(b"%%EOF"):
            raise PDFGenerationError("Generated PDF missing %%EOF trailer")
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        _LOG.info(
            "pdf_writer_complete",
            extra={
                **context,
                "duration_ms": duration_ms,
                "bytes": len(pdf_bytes),
                "sections": len(formatted_sections),
                "indices_present": {k: bool(v) for k, v in indices.items()},
            },
        )
        return pdf_bytes


def _cli(argv: Optional[Iterable[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Render MCC medical summary PDF (Cloud Run job aware)."
    )
    parser.add_argument("--input", required=True, help="Summary JSON path.")
    parser.add_argument("--output", help="Optional local path to write PDF bytes.")
    parser.add_argument("--job-id", help="Pipeline job identifier to update state.")
    parser.add_argument(
        "--upload-gcs", help="Destination gs://bucket/object for PDF upload."
    )
    parser.add_argument(
        "--if-generation-match",
        type=int,
        dest="if_generation_match",
        default=0,
        help="ifGenerationMatch precondition for GCS upload (default 0 to prevent duplicate overwrites).",
    )
    parser.add_argument(
        "--sign-url-ttl",
        type=int,
        default=int(os.getenv("PDF_SIGNED_URL_TTL", "3600")),
        help="TTL in seconds for signed URL generation.",
    )
    parser.add_argument(
        "--skip-signed-url",
        action="store_true",
        help="Disable signed URL generation even when uploading to GCS.",
    )
    parser.add_argument(
        "--kms-key",
        help="Cloud KMS key resource to sign URLs (requires SIGNED_URL_SERVICE_ACCOUNT).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    writer = PDFWriterRefactored()
    input_arg = args.input
    if "gs://" in input_arg:
        input_arg = input_arg[input_arg.index("gs://") :]
    elif input_arg.startswith("gs:/"):
        input_arg = input_arg.replace("gs:/", "gs://", 1)
    summary_path: Path | str
    if input_arg.startswith("gs://"):
        summary_path = input_arg
    else:
        summary_path = Path(input_arg)
    pdf_path = Path(args.output) if args.output else None
    cfg = get_config()
    schema_version = cfg.summary_schema_version
    job_snapshot = None
    trace_id: Optional[str] = None
    document_id: Optional[str] = None
    attempt_value = 1

    state_store: PipelineStateStore | None = None
    base_metadata: Dict[str, Any] = {}
    if args.job_id:
        try:
            state_store = create_state_store_from_env()
            job_snapshot = state_store.get_job(args.job_id)
            if job_snapshot:
                base_metadata = dict(job_snapshot.metadata)
                trace_id = getattr(job_snapshot, "trace_id", None)
                document_id = getattr(job_snapshot, "pdf_uri", None) or getattr(
                    job_snapshot, "object_uri", None
                )
                if isinstance(job_snapshot.retries, dict):
                    attempt_value = job_snapshot.retries.get("PDF_JOB", 0) + 1
                state_store.mark_status(
                    args.job_id,
                    PipelineStatus.PDF_SCHEDULED,
                    stage="PDF_JOB",
                    message="PDF writer started",
                    extra={
                        "input_path": (
                            input_arg
                            if isinstance(summary_path, str)
                            else str(summary_path.resolve())
                        )
                    },
                )
        except Exception as exc:  # pragma: no cover - defensive
            _LOG.exception(
                "pdf_job_state_init_failed",
                extra={"job_id": args.job_id, "error": str(exc)},
            )
            state_store = None

    try:
        summary_payload = _load_summary(summary_path)
        pdf_log_context: Dict[str, Any] = {
            "job_id": args.job_id,
            "trace_id": trace_id,
            "document_id": document_id
            or (input_arg if isinstance(summary_path, str) else str(summary_path)),
            "shard_id": "aggregate",
            "schema_version": schema_version,
            "attempt": attempt_value,
            "component": "pdf_writer",
            "severity": "INFO",
        }
        if trace_id:
            pdf_log_context["logging.googleapis.com/trace"] = (
                f"projects/{cfg.project_id}/traces/{trace_id}"
            )
        pdf_bytes = writer.build(summary_payload, log_context=pdf_log_context)
    except Exception as exc:
        if state_store and args.job_id:
            try:
                state_store.mark_status(
                    args.job_id,
                    PipelineStatus.FAILED,
                    stage="PDF_JOB",
                    message=str(exc),
                    extra={"error": str(exc), "phase": "pdf_generation"},
                    updates={"last_error": {"stage": "pdf_writer", "error": str(exc)}},
                )
            except Exception:  # pragma: no cover - best effort
                _LOG.exception(
                    "pdf_job_state_mark_failed", extra={"job_id": args.job_id}
                )
        raise

    if pdf_path:
        _write_pdf(pdf_path, pdf_bytes)
    else:
        sys.stdout.buffer.write(pdf_bytes)

    blob = None
    pdf_uri = args.upload_gcs
    signed_url: Optional[str] = None
    if args.upload_gcs:
        try:
            blob = _upload_pdf_to_gcs(
                pdf_bytes, args.upload_gcs, if_generation_match=args.if_generation_match
            )
            pdf_uri = f"gs://{blob.bucket.name}/{blob.name}"
            if not args.skip_signed_url:
                signed_url = _generate_signed_url(
                    blob, args.sign_url_ttl, kms_key=args.kms_key
                )
        except Exception as exc:
            if state_store and args.job_id:
                try:
                    state_store.mark_status(
                        args.job_id,
                        PipelineStatus.FAILED,
                        stage="PDF_JOB_UPLOAD",
                        message=str(exc),
                        extra={"error": str(exc), "phase": "pdf_upload"},
                        updates={
                            "last_error": {"stage": "pdf_upload", "error": str(exc)}
                        },
                    )
                except Exception:  # pragma: no cover - best effort
                    _LOG.exception(
                        "pdf_job_state_upload_failed", extra={"job_id": args.job_id}
                    )
            raise

    if state_store and args.job_id:
        try:
            metadata_patch = {
                "pdf_bytes": len(pdf_bytes),
                "pdf_uploaded": bool(pdf_uri),
            }
            merged_metadata = _merge_dicts(base_metadata, metadata_patch)
            state_store.mark_status(
                args.job_id,
                PipelineStatus.PDF_DONE,
                stage="PDF_JOB",
                message="PDF generated",
                extra={"bytes": len(pdf_bytes), "uploaded": bool(pdf_uri)},
                updates={
                    "pdf_uri": pdf_uri,
                    "signed_url": signed_url,
                    "metadata": merged_metadata,
                },
            )
            if pdf_uri:
                state_store.mark_status(
                    args.job_id,
                    PipelineStatus.UPLOADED,
                    stage="PDF_JOB",
                    message="PDF uploaded to GCS",
                    extra={"pdf_uri": pdf_uri, "signed_url": signed_url},
                )
        except Exception:  # pragma: no cover - best effort
            _LOG.exception(
                "pdf_job_state_complete_failed", extra={"job_id": args.job_id}
            )


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    _cli()


__all__ = ["PDFWriterRefactored"]
