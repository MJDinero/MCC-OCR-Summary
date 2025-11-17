"""CLI entrypoint for the refactored summariser."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.config import get_config
from src.errors import SummarizationError
from src.services.pipeline import (
    PipelineStateStore,
    PipelineStatus,
    create_state_store_from_env,
)
from src.services.supervisor import CommonSenseSupervisor
from src.services.summarization.backend import (
    ChunkSummaryBackend,
    HeuristicChunkBackend,
    OpenAIResponsesBackend,
)
from src.services.summarization.controller import RefactoredSummariser
from src.utils.secrets import SecretResolutionError, resolve_secret_env

_LOG = logging.getLogger("summariser.cli")


def _normalise_document_payload(
    data: Dict[str, Any],
) -> tuple[str, Dict[str, Any], List[Dict[str, Any]]]:
    if not isinstance(data, dict):
        raise SummarizationError("Input payload must be a JSON object.")

    metadata: Dict[str, Any] = {}
    if isinstance(data.get("metadata"), dict):
        metadata = dict(data["metadata"])

    document: Dict[str, Any] | None = (
        data.get("document") if isinstance(data.get("document"), dict) else None
    )
    if document:
        doc_metadata = document.get("metadata")
        if isinstance(doc_metadata, dict):
            metadata = _merge_dicts(metadata, doc_metadata)
    else:
        document = data

    if not isinstance(document, dict):
        raise SummarizationError("Input payload must be a JSON object.")

    pages_raw = document.get("pages")
    pages: List[Dict[str, Any]] = (
        [page for page in pages_raw if isinstance(page, dict)]
        if isinstance(pages_raw, list)
        else []
    )

    text_val = document.get("text")
    text = text_val.strip() if isinstance(text_val, str) else ""
    if not text and pages:
        text = " ".join(
            (page.get("text") or "").strip() for page in pages if isinstance(page, dict)
        ).strip()
    if not text:
        raise SummarizationError("Input JSON missing 'text' or 'pages' fields.")

    return text, metadata, pages


def _split_gcs_uri(gcs_uri: str) -> Tuple[str, str]:
    if not gcs_uri.startswith("gs://"):
        raise SummarizationError("GCS URI must start with gs://")
    bucket, _, blob = gcs_uri[5:].partition("/")
    if not bucket or not blob:
        raise SummarizationError("Invalid GCS URI; expected gs://bucket/object")
    return bucket, blob


def _load_input_payload_from_gcs(
    gcs_uri: str,
) -> tuple[str, Dict[str, Any], List[Dict[str, Any]]]:  # pragma: no cover - optional
    try:
        from google.cloud import storage  # type: ignore[attr-defined]
    except Exception as exc:
        raise SummarizationError(f"google-cloud-storage unavailable: {exc}") from exc

    bucket_name, object_name = _split_gcs_uri(gcs_uri)
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)

    payload_bytes: bytes | None = None
    try:
        payload_bytes = blob.download_as_bytes()
    except Exception:
        payload_bytes = None

    if payload_bytes:
        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise SummarizationError(
                f"Invalid JSON payload at {gcs_uri}: {exc}"
            ) from exc
        return _normalise_document_payload(payload)

    prefix = object_name
    if prefix and not prefix.endswith("/"):
        prefix = prefix.rsplit("/", 1)[0] + "/"

    documents: List[Dict[str, Any]] = []
    for candidate in client.list_blobs(bucket_name, prefix=prefix):
        if candidate.name == object_name or not candidate.name.endswith(".json"):
            continue
        try:
            doc_payload = json.loads(candidate.download_as_bytes().decode("utf-8"))
        except Exception:
            continue
        documents.append(doc_payload)

    if not documents:
        raise FileNotFoundError(f"Input payload not found: {gcs_uri}")

    combined_metadata: Dict[str, Any] = {}
    combined_pages: List[Dict[str, Any]] = []
    combined_text_parts: List[str] = []

    for doc in documents:
        try:
            text, metadata, pages = _normalise_document_payload(doc)
        except SummarizationError:
            continue
        if metadata:
            combined_metadata = _merge_dicts(combined_metadata, metadata)
        combined_pages.extend(pages)
        if text:
            combined_text_parts.append(text)

    if not combined_pages and not combined_text_parts:
        raise SummarizationError("No readable OCR payloads found in GCS prefix.")

    combined: Dict[str, Any] = {"pages": combined_pages}
    if combined_metadata:
        combined["metadata"] = combined_metadata
    if combined_text_parts:
        combined["text"] = "\n".join(combined_text_parts)

    return _normalise_document_payload(combined)


def _load_input_payload(
    path: Path | str,
) -> tuple[str, Dict[str, Any], List[Dict[str, Any]]]:
    raw_path = str(path)
    if raw_path.startswith("gs://"):
        return _load_input_payload_from_gcs(raw_path)

    local_path = Path(raw_path)
    if not local_path.exists():
        raise FileNotFoundError(f"Input payload not found: {local_path}")
    try:
        payload = json.loads(local_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SummarizationError(
            f"Invalid JSON payload at {local_path}: {exc}"
        ) from exc
    return _normalise_document_payload(payload)


def _write_output(path: Path, summary: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


def _upload_summary_to_gcs(  # pragma: no cover - requires GCS interaction
    gcs_uri: str,
    summary: Dict[str, Any],
    *,
    if_generation_match: int | None = 0,
) -> str:
    try:
        from google.cloud import storage  # type: ignore
    except Exception as exc:
        raise SummarizationError(f"google-cloud-storage unavailable: {exc}") from exc

    bucket_name, object_name = _split_gcs_uri(gcs_uri)
    client = storage.Client()
    blob = client.bucket(bucket_name).blob(object_name)
    payload = json.dumps(
        summary, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    upload_kwargs: Dict[str, Any] = {"content_type": "application/json"}
    if if_generation_match is not None and if_generation_match >= 0:
        upload_kwargs["if_generation_match"] = if_generation_match
    blob.upload_from_string(payload, **upload_kwargs)
    gcs_path = f"gs://{blob.bucket.name}/{blob.name}"
    _LOG.info("summary_uploaded_gcs", extra={"gcs_uri": gcs_path, "bytes": len(payload)})
    return gcs_path


def _merge_dicts(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    merged.update(patch)
    return merged


def run_cli(argv: Optional[Iterable[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate MCC medical summaries using the refactored summariser."
    )
    parser.add_argument("--input", required=True, help="Path to OCR JSON payload.")
    parser.add_argument("--output", help="Optional path to write JSON summary locally.")
    parser.add_argument("--output-gcs", help="Optional GCS URI to upload the summary JSON.")
    parser.add_argument(
        "--gcs-if-generation",
        type=int,
        default=0,
        help="ifGenerationMatch precondition for GCS uploads (set -1 to disable).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Use heuristic backend only.")
    parser.add_argument(
        "--model",
        default=os.getenv("OPENAI_MODEL") or "gpt-4o-mini",
        help="OpenAI model to use when not running in --dry-run mode.",
    )
    parser.add_argument("--api-key", help="Explicit OpenAI API key (fallback to env/secret)")
    parser.add_argument(
        "--target-chars",
        type=int,
        default=int(os.getenv("REF_SUMMARISER_TARGET_CHARS", "2400")),
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=int(os.getenv("REF_SUMMARISER_MAX_CHARS", "10000")),
    )
    parser.add_argument(
        "--overlap-chars",
        type=int,
        default=int(os.getenv("REF_SUMMARISER_OVERLAP_CHARS", "320")),
    )
    parser.add_argument(
        "--min-summary-chars",
        type=int,
        default=int(os.getenv("REF_SUMMARISER_MIN_SUMMARY_CHARS", "480")),
    )
    parser.add_argument("--job-id", help="Optional pipeline job id for state store updates.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    text, metadata, pages = _load_input_payload(args.input)
    supervisor = CommonSenseSupervisor()

    backend_label = "heuristic" if args.dry_run else "openai"
    if args.dry_run:
        backend: ChunkSummaryBackend = HeuristicChunkBackend()
        _LOG.info("heuristic_backend_active", extra={"input_chars": len(text)})
    else:
        api_key = args.api_key
        if not api_key:
            project_id = os.getenv("PROJECT_ID")
            try:
                api_key = resolve_secret_env("OPENAI_API_KEY", project_id=project_id)
            except SecretResolutionError:
                api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            parser.error("OPENAI_API_KEY must be set (or --api-key provided) when not using --dry-run.")
        backend = OpenAIResponsesBackend(model=args.model, api_key=api_key)
        _LOG.info("openai_backend_active", extra={"model": args.model})

    def _new_summariser(active_backend: ChunkSummaryBackend) -> RefactoredSummariser:
        return RefactoredSummariser(
            backend=active_backend,
            target_chars=args.target_chars,
            max_chars=args.max_chars,
            overlap_chars=args.overlap_chars,
            min_summary_chars=args.min_summary_chars,
        )

    summariser = _new_summariser(backend)

    doc_stats = supervisor.collect_doc_stats(text=text, pages=pages, file_bytes=None)
    state_store: PipelineStateStore | None = None
    base_metadata: Dict[str, Any] = {}
    job_snapshot = None
    attempt_value = 1
    trace_id: Optional[str] = None
    document_id: Optional[str] = None
    if args.job_id:
        try:
            state_store = create_state_store_from_env()
            job_snapshot = state_store.get_job(args.job_id)
            if job_snapshot:
                base_metadata = dict(job_snapshot.metadata)
                trace_id = job_snapshot.trace_id
                document_id = job_snapshot.object_uri or job_snapshot.object_name
                if isinstance(job_snapshot.retries, dict):
                    attempt_value = job_snapshot.retries.get("SUMMARY_JOB", 0) + 1
            state_store.mark_status(
                args.job_id,
                PipelineStatus.SUMMARY_SCHEDULED,
                stage="SUMMARY_JOB",
                message="Summariser job started",
                extra={
                    "input_path": (
                        args.input if args.input.startswith("gs://") else str(Path(args.input).resolve())
                    ),
                    "estimated_pages": len(pages),
                    "input_chars": len(text),
                },
            )
        except Exception as exc:
            _LOG.exception(
                "summary_job_state_init_failed",
                extra={"job_id": args.job_id, "error": str(exc)},
            )
            state_store = None

    validation: Dict[str, Any] = {}
    summary: Dict[str, Any] = {}
    failure_phase = "summarisation"
    summarise_started = time.perf_counter()
    try:
        try:
            try:
                summary = summariser.summarise(text, doc_metadata=metadata)
            except SummarizationError as exc:
                if not args.dry_run and isinstance(summariser.backend, OpenAIResponsesBackend):
                    _LOG.warning(
                        "summariser_cli_backend_fallback",
                        extra={"error": str(exc), "backend": "openai"},
                    )
                    backend_label = "heuristic_fallback"
                    summariser = _new_summariser(HeuristicChunkBackend())
                    summary = summariser.summarise(text, doc_metadata=metadata)
                else:
                    raise
            failure_phase = "supervisor"
            validation = supervisor.validate(
                ocr_text=text, summary=summary, doc_stats=doc_stats, retries=0
            )
        except SummarizationError as exc:
            if state_store and args.job_id:
                try:
                    state_store.mark_status(
                        args.job_id,
                        PipelineStatus.FAILED,
                        stage="SUMMARY_JOB",
                        message=str(exc),
                        extra={"error": str(exc)},
                        updates={"last_error": {"stage": failure_phase, "error": str(exc)}},
                    )
                except Exception:
                    _LOG.exception(
                        "summary_job_state_failure", extra={"job_id": args.job_id}
                    )
            raise

        backend_label = backend_label or backend.__class__.__name__
        if not validation.get("supervisor_passed", False):
            reason = validation.get("reason") or "supervisor_rejected"
            if args.dry_run or backend_label == "heuristic_fallback":
                override_mode = "dry_run" if args.dry_run else "heuristic_fallback"
                validation["override_mode"] = override_mode
                validation["override_reason"] = reason
                validation["supervisor_passed"] = True
                log_event = (
                    "supervisor_override_dry_run"
                    if args.dry_run
                    else "supervisor_override_heuristic"
                )
                _LOG.warning(
                    log_event,
                    extra={
                        "reason": reason,
                        "input_chars": len(text),
                        "pages": len(pages),
                    },
                )
            else:
                raise SummarizationError(f"Supervisor validation failed: {reason}")
    except Exception as exc:
        if state_store and args.job_id:
            try:
                stage_label = (
                    "SUPERVISOR" if failure_phase == "supervisor" else "SUMMARY_JOB"
                )
                state_store.mark_status(
                    args.job_id,
                    PipelineStatus.FAILED,
                    stage=stage_label,
                    message=str(exc),
                    extra={
                        "error": str(exc),
                        "phase": failure_phase,
                        "summary_backend": backend_label,
                    },
                    updates={"last_error": {"stage": failure_phase, "error": str(exc)}},
                )
            except Exception:
                _LOG.exception(
                    "summary_job_state_failure_mark_failed",
                    extra={"job_id": args.job_id},
                )
        raise

    summary_gcs_uri: Optional[str] = None
    if args.output:
        _write_output(Path(args.output), summary)
    if args.output_gcs:
        try:
            if_generation = None if args.gcs_if_generation < 0 else args.gcs_if_generation
            summary_gcs_uri = _upload_summary_to_gcs(
                args.output_gcs, summary, if_generation_match=if_generation
            )
        except Exception as exc:
            if state_store and args.job_id:
                try:
                    state_store.mark_status(
                        args.job_id,
                        PipelineStatus.FAILED,
                        stage="SUMMARY_JOB",
                        message=str(exc),
                        extra={"error": str(exc), "phase": "summary_upload"},
                        updates={
                            "last_error": {"stage": "summary_upload", "error": str(exc)}
                        },
                    )
                except Exception:
                    _LOG.exception(
                        "summary_job_state_upload_failed",
                        extra={"job_id": args.job_id},
                    )
            raise

    schema_version = os.getenv("SUMMARY_SCHEMA_VERSION", "2025-11-16")
    if state_store and args.job_id:
        try:
            summary_metadata: Dict[str, Any] = {
                "summary_sections": [
                    key for key in summary.keys() if not key.startswith("_")
                ],
                "summary_char_length": sum(
                    len(str(value or "")) for value in summary.values()
                ),
                "summary_generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "supervisor_validation": validation,
                "summary_schema_version": schema_version,
                "summary_backend": backend_label,
            }
            if summary_gcs_uri:
                summary_metadata["summary_gcs_uri"] = summary_gcs_uri
            merged_metadata = _merge_dicts(base_metadata, summary_metadata)
            state_store.mark_status(
                args.job_id,
                PipelineStatus.SUMMARY_DONE,
                stage="SUMMARY_JOB",
                message="Summary generated",
                extra={
                    "summary_char_length": summary_metadata["summary_char_length"],
                    "summary_gcs_uri": summary_gcs_uri,
                    "summary_backend": backend_label,
                },
                updates={"metadata": merged_metadata},
            )
            state_store.mark_status(
                args.job_id,
                PipelineStatus.SUPERVISOR_DONE,
                stage="SUPERVISOR",
                message="Supervisor validation complete",
                extra={
                    "supervisor_passed": bool(validation.get("supervisor_passed")),
                    "length_score": validation.get("length_score"),
                    "content_alignment": validation.get("content_alignment"),
                },
            )
        except Exception:
            _LOG.exception(
                "summary_job_state_complete_failed", extra={"job_id": args.job_id}
            )
    duration_ms = int((time.perf_counter() - summarise_started) * 1000)
    trace_field: Optional[str] = None
    if trace_id:
        project_id = os.getenv("PROJECT_ID") or get_config().project_id
        if project_id:
            trace_field = f"projects/{project_id}/traces/{trace_id}"
    log_extra = {
        "job_id": args.job_id,
        "trace_id": trace_id,
        "document_id": document_id,
        "shard_id": "aggregate",
        "duration_ms": duration_ms,
        "schema_version": schema_version,
        "attempt": attempt_value,
        "component": "summary_job",
        "severity": "INFO",
    }
    if summary_gcs_uri:
        log_extra["summary_gcs_uri"] = summary_gcs_uri
    if trace_field and trace_id:
        log_extra["logging.googleapis.com/trace"] = trace_field
    _LOG.info("summary_done", extra=log_extra)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


__all__ = ["run_cli"]
