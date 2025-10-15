"""Batch (asynchronous) Document AI processing helper.

Provides a thin abstraction over the Document AI BatchProcessRequest so the
rest of the service only needs to supply input and output GCS URIs. The helper
uploads a local PDF to the intake bucket if required, triggers the batch
operation, polls until completion (or timeout) and then assembles a normalised
document structure by reading the JSON output files written by Document AI.

Returned structure mirrors the synchronous path so downstream components (e.g.
summariser) can remain agnostic of sync vs async execution. Additional batch
metadata is exposed under the 'batch_metadata' key for observability.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from google.api_core.client_options import ClientOptions
from google.api_core import exceptions as gexc
from google.cloud import documentai_v1 as documentai
from google.cloud import storage  # type: ignore[attr-defined]

from src.errors import OCRServiceError, ValidationError
from src.config import get_config, AppConfig

_LOG = logging.getLogger("ocr_service.batch")

POLL_INTERVAL_SECONDS = 5
TIMEOUT_SECONDS = 30 * 60  # 30 minutes


def _gcs_uri(bucket: str, prefix: str) -> str:
    if not prefix.endswith('/'):
        prefix += '/'
    return f"gs://{bucket}/{prefix}"


def _normalise(doc: Dict[str, Any]) -> Dict[str, Any]:
    pages_out: List[Dict[str, Any]] = []
    pages = doc.get("pages") or []
    for idx, p in enumerate(pages, start=1):
        text = ""
        if isinstance(p, dict):
            layout = p.get("layout") or {}
            if isinstance(layout, dict):
                text = layout.get("text", "") or p.get("text", "")
            else:  # pragma: no cover - defensive
                text = p.get("text", "")
        pages_out.append({"page_number": idx, "text": text})
    full_text = doc.get("text") or " ".join(pg["text"] for pg in pages_out)
    return {"text": full_text, "pages": pages_out}


def _read_output_documents(storage_client: storage.Client, output_prefix: str) -> Dict[str, Any]:
    """Aggregate all Document JSON outputs under the given GCS prefix.

    Document AI writes one or more JSON files (sharded). We concatenate page
    lists and build a combined text. If no JSON files are found we raise an
    error to surface misconfiguration early.
    """
    if not output_prefix.startswith("gs://"):
        raise OCRServiceError("output_prefix must be a GCS URI")
    # Split gs://bucket/path/
    without_scheme = output_prefix[5:]
    bucket_name, _, prefix = without_scheme.partition('/')
    blobs = list(storage_client.list_blobs(bucket_name, prefix=prefix))
    json_blobs = [b for b in blobs if b.name.endswith(".json")]
    if not json_blobs:
        raise OCRServiceError("No JSON outputs found in batch output prefix")
    combined_pages: List[Any] = []
    full_text_parts: List[str] = []
    for blob in json_blobs:
        try:
            data = blob.download_as_bytes()
            parsed = json.loads(data.decode('utf-8'))
            # Each JSON may contain a Document object or wrapper with 'document'
            doc_obj = parsed.get('document') if isinstance(parsed, dict) else None
            if not doc_obj:
                doc_obj = parsed  # Accept raw Document
            if isinstance(doc_obj, dict):
                pages = doc_obj.get('pages') or []
                combined_pages.extend(pages)
                txt = doc_obj.get('text') or ''
                if txt:
                    full_text_parts.append(txt)
        except Exception as exc:  # pragma: no cover - best effort; fail fast
            raise OCRServiceError(f"Failed parsing batch output JSON: {exc}") from exc
    merged = {"text": "\n".join(full_text_parts), "pages": combined_pages}
    return merged


@dataclass
class _BatchClients:
    docai: documentai.DocumentProcessorServiceClient
    storage: storage.Client


def _default_clients(region: str) -> _BatchClients:
    endpoint = f"{region}-documentai.googleapis.com"
    return _BatchClients(
        docai=documentai.DocumentProcessorServiceClient(client_options=ClientOptions(api_endpoint=endpoint)),
        storage=storage.Client(),
    )


def batch_process_documents_gcs(
    input_uri: str,
    output_uri: Optional[str],
    processor_id: str,
    region: str,
    *,
    project_id: Optional[str] = None,
    clients: Optional[_BatchClients] = None,
) -> Dict[str, Any]:
    """Run Document AI batch process for a (potentially large) PDF.

    Args:
        input_uri: Either a gs:// URI or local filesystem path to a PDF.
        output_uri: Optional base gs:// URI. If omitted, a unique prefix in the
            default output bucket is created.
        processor_id: Document AI processor id.
        region: Regional endpoint.
        project_id: GCP project (taken from global config if not provided).
        clients: Optionally provide preconstructed clients (for tests).
    Returns:
        Normalised document dict (text, pages) with additional keys:
            batch_metadata: {status, output_uri, pages_processed, operation_name}
    Raises:
        OCRServiceError / ValidationError on failures.
    """
    cfg: AppConfig = get_config()
    project_id = project_id or cfg.project_id
    if not all([project_id, region, processor_id]):
        raise ValidationError("project_id, region, processor_id required for batch processing")

    clients = clients or _default_clients(region)
    storage_client = clients.storage
    docai_client = clients.docai
    kms_key = getattr(cfg, "cmek_key_name", None)

    # Ensure input is in GCS
    intake_bucket = (cfg.intake_gcs_bucket or "").strip() or "quantify-agent-intake"
    output_bucket = (cfg.output_gcs_bucket or cfg.summary_bucket or "").strip() or "quantify-agent-output"

    if input_uri.startswith("gs://"):
        gcs_input_uri = input_uri
    else:
        local_path = Path(input_uri)
        if not local_path.exists():
            raise ValidationError(f"Input file not found: {local_path}")
        if local_path.suffix.lower() != '.pdf':
            raise ValidationError("Only PDF inputs supported for batch")
        target_name = f"uploads/{uuid.uuid4().hex}-{local_path.name}".replace('//', '/')
        gcs_input_uri = _gcs_uri(intake_bucket, target_name)
        # Upload
        bucket = storage_client.bucket(intake_bucket)
        blob = bucket.blob(target_name)
        if kms_key:
            setattr(blob, "kms_key_name", kms_key)
        _LOG.info("batch_upload_start", extra={"gcs_uri": gcs_input_uri, "size_bytes": local_path.stat().st_size})
        blob.upload_from_filename(str(local_path))
        _LOG.info("batch_upload_complete", extra={"gcs_uri": gcs_input_uri})

    # Prepare output prefix
    if output_uri and output_uri.startswith("gs://"):
        output_prefix = output_uri.rstrip('/') + '/'
    else:
        unique_prefix = f"batch/{time.strftime('%Y%m%d')}/{uuid.uuid4().hex}/"
        output_prefix = _gcs_uri(output_bucket, unique_prefix)

    name = f"projects/{project_id}/locations/{region}/processors/{processor_id}"
    request = {
        "name": name,
        "input_documents": {
            "gcs_documents": {
                "documents": [
                    {"gcs_uri": gcs_input_uri, "mime_type": "application/pdf"}
                ]
            }
        },
        "document_output_config": {
            "gcs_output_config": {"gcs_uri": output_prefix, **({"kms_key_name": kms_key} if kms_key else {})}
        },
    }
    if kms_key:
        request["encryption_spec"] = {"kms_key_name": kms_key}

    _LOG.info(
        "batch_start",
        extra={
            "input_uri": gcs_input_uri,
            "output_prefix": output_prefix,
            "processor_id": processor_id,
        },
    )
    try:
        operation = docai_client.batch_process_documents(request=request)
    except gexc.GoogleAPICallError as exc:
        raise OCRServiceError(f"Batch process submission failed: {exc}") from exc
    except Exception as exc:  # pragma: no cover - unexpected
        raise OCRServiceError(f"Unexpected batch submission error: {exc}") from exc

    # Poll loop with manual timeout so we can log progress.
    start = time.time()
    while not operation.done():  # type: ignore[attr-defined]
        elapsed = time.time() - start
        if elapsed > TIMEOUT_SECONDS:
            raise OCRServiceError("Batch operation timeout exceeded")
        _LOG.info(
            "batch_poll",
            extra={"operation": getattr(operation, 'operation', getattr(operation, 'name', 'unknown')), "elapsed_s": round(elapsed, 1)},
        )
        time.sleep(POLL_INTERVAL_SECONDS)

    # Retrieve final result / raise if failed.
    try:
        operation.result()  # will raise on failure
    except Exception as exc:
        raise OCRServiceError(f"Batch operation failed: {exc}") from exc

    # Attempt to derive pages processed from metadata
    pages_processed = None
    meta = getattr(operation, 'metadata', None)
    if meta and hasattr(meta, 'individual_process_statuses'):
        try:  # pragma: no cover - best effort
            pages_processed = len(meta.individual_process_statuses)
        except Exception:
            pages_processed = None

    # Read output docs & normalise
    combined_doc = _read_output_documents(storage_client, output_prefix)
    normalised = _normalise(combined_doc)
    normalised["batch_metadata"] = {
        "status": "succeeded",
        "output_uri": output_prefix,
        "pages_processed": pages_processed or len(normalised.get("pages", [])),
        "operation_name": getattr(operation, 'name', None),
    }

    _LOG.info(
        "batch_complete",
        extra={
            "output_uri": output_prefix,
            "pages": normalised["batch_metadata"]["pages_processed"],
        },
    )
    return normalised


__all__ = ["batch_process_documents_gcs"]
