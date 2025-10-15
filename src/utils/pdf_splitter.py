"""Document AI powered PDF splitter utility.

This module replaces the legacy PyPDF-based splitter with a thin wrapper around
Document AI's *Document Splitter* processor. The splitter writes output PDFs to
a caller-specified GCS prefix and returns a manifest describing the emitted
segments. All writes use `ifGenerationMatch=0` to guarantee idempotency.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import List, Optional, Tuple

from src.errors import ValidationError

try:  # pragma: no cover - optional at test time
    from google.cloud import documentai_v1 as documentai  # type: ignore
    from google.cloud import storage  # type: ignore
    from google.api_core.client_options import ClientOptions  # type: ignore
except Exception:  # pragma: no cover
    documentai = None  # type: ignore
    storage = None  # type: ignore
    ClientOptions = None  # type: ignore

_LOG = logging.getLogger("pdf_splitter")
POLL_SECONDS = 6
POLL_TIMEOUT = 30 * 60  # 30 minutes


@dataclass
class SplitResult:
    parts: List[str]
    manifest_gcs_uri: str


def _ensure_clients(
    region: str,
    storage_client: Optional[storage.Client],
    docai_client: Optional[documentai.DocumentProcessorServiceClient],
) -> Tuple[storage.Client, documentai.DocumentProcessorServiceClient]:
    if documentai is None or storage is None:
        raise RuntimeError("google-cloud-documentai and google-cloud-storage are required for PDF splitting")
    client_options = ClientOptions(api_endpoint=f"{region}-documentai.googleapis.com")
    docai_client = docai_client or documentai.DocumentProcessorServiceClient(client_options=client_options)
    storage_client = storage_client or storage.Client()
    return storage_client, docai_client


def split_pdf_by_page_limit(
    input_uri: str,
    *,
    project_id: str,
    location: str,
    splitter_processor_id: str,
    output_prefix: Optional[str] = None,
    storage_client: Optional[storage.Client] = None,
    docai_client: Optional[documentai.DocumentProcessorServiceClient] = None,
) -> SplitResult:
    """Invoke the Document AI splitter and return part URIs + manifest."""
    if not input_uri.startswith("gs://"):
        raise ValidationError("split_pdf_by_page_limit requires a gs:// input")
    if not splitter_processor_id:
        raise ValidationError("splitter_processor_id is required")

    storage_client, docai_client = _ensure_clients(location, storage_client, docai_client)

    if output_prefix and not output_prefix.startswith("gs://"):
        raise ValidationError("output_prefix must be gs:// or omitted")
    if not output_prefix:
        bucket_part = input_uri[5:].split("/", 1)[0]
        output_prefix = f"gs://{bucket_part}/split/{uuid.uuid4().hex}/"
    if not output_prefix.endswith("/"):
        output_prefix += "/"

    name = f"projects/{project_id}/locations/{location}/processors/{splitter_processor_id}"
    request = {
        "name": name,
        "input_documents": {
            "gcs_documents": {
                "documents": [
                    {"gcs_uri": input_uri, "mime_type": "application/pdf"},
                ]
            }
        },
        "document_output_config": {
            "gcs_output_config": {"gcs_uri": output_prefix}
        },
    }

    _LOG.info(
        "splitter_start",
        extra={"input_uri": input_uri, "output_prefix": output_prefix, "processor": splitter_processor_id},
    )
    operation = docai_client.batch_process_documents(request=request)
    start = time.time()
    while True:
        if operation.done():  # type: ignore[attr-defined]
            break
        elapsed = time.time() - start
        if elapsed > POLL_TIMEOUT:
            raise RuntimeError("Document AI splitter timed out")
        _LOG.debug("splitter_poll", extra={"operation": getattr(operation, "name", "unknown"), "elapsed_s": round(elapsed, 1)})
        time.sleep(POLL_SECONDS)

    try:
        operation.result()
    except Exception as exc:  # pragma: no cover - bubble failure with context
        raise RuntimeError(f"Document AI splitter failed: {exc}") from exc

    parts = _collect_output_parts(storage_client, output_prefix)
    manifest_uri = _write_manifest(storage_client, output_prefix, input_uri, parts)
    _LOG.info(
        "splitter_complete",
        extra={"parts": len(parts), "manifest": manifest_uri},
    )
    return SplitResult(parts=parts, manifest_gcs_uri=manifest_uri)


def _collect_output_parts(storage_client: storage.Client, prefix: str) -> List[str]:
    bucket_name, object_prefix = _split_gs_uri(prefix)
    blobs = list(storage_client.list_blobs(bucket_name, prefix=object_prefix))
    parts = [f"gs://{bucket_name}/{blob.name}" for blob in blobs if blob.name.lower().endswith(".pdf")]
    if not parts:
        raise RuntimeError("Document AI splitter produced no PDF parts")
    parts.sort()
    return parts


def _write_manifest(storage_client: storage.Client, prefix: str, input_uri: str, parts: List[str]) -> str:
    bucket_name, object_prefix = _split_gs_uri(prefix)
    manifest_path = object_prefix.rstrip("/") + "/manifest.json"
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(manifest_path)
    payload = {
        "input_uri": input_uri,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "parts": [
            {"part": idx + 1, "gcs_uri": uri}
            for idx, uri in enumerate(parts)
        ],
    }
    blob.upload_from_string(
        json.dumps(payload, separators=(",", ":")),
        content_type="application/json",
        if_generation_match=0,
    )
    return f"gs://{bucket_name}/{manifest_path}"


def _split_gs_uri(uri: str) -> tuple[str, str]:
    without = uri[5:]
    bucket, _, path = without.partition("/")
    if not bucket:
        raise ValidationError("Invalid gs:// URI")
    return bucket, path


__all__ = ["split_pdf_by_page_limit", "SplitResult"]
