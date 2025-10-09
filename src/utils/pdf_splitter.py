"""Utility for splitting large PDFs into smaller parts and uploading to GCS.

The splitter is conservative: if the input PDF has <= max_pages it returns the
original path untouched. For larger PDFs we write sequential chunks of up to
`max_pages` pages into a temporary directory, upload each to a deterministic
GCS prefix and emit a manifest.json recording part boundaries and remote URIs.

Returned value is a list of gs:// URIs for the uploaded PDF parts in order.

Design goals:
- Avoid loading full PDF into memory repeatedly (stream page objects)
- Generate stable ordering and explicit zero-padded part numbers for traceability
- Emit structured logging hooks so callers can correlate split + batch events

Environment / Configuration assumptions (not tightly coupled to AppConfig to
keep the utility re-usable in tests):
- Intake bucket used for uploads: quantify-agent-intake
- GCS layout: splits/<uuid4>/part_0001.pdf, manifest.json

The manifest structure:
{
  "original_file": "local/or/gcs/path.pdf",
  "total_pages": 824,
  "max_pages": 200,
  "parts": [
     {"part": 1, "name": "part_0001.pdf", "page_start": 1,   "page_end": 200, "gcs_uri": "gs://.../part_0001.pdf"},
     {"part": 2, "name": "part_0002.pdf", "page_start": 201, "page_end": 400, "gcs_uri": "gs://.../part_0002.pdf"},
     ...
  ]
}
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import logging
import tempfile
import uuid
import json
from typing import List, Sequence

from google.cloud import storage
from PyPDF2 import PdfReader, PdfWriter

_LOG = logging.getLogger("pdf_splitter")

INTAKE_BUCKET = "quantify-agent-intake"


@dataclass
class SplitResult:
    parts: List[str]  # GCS URIs
    manifest_gcs_uri: str


def _ensure_local_path(input_path: str | Path) -> Path:
    p = Path(input_path)
    if not p.exists():  # Accept gs:// here later if needed
        raise FileNotFoundError(f"Input PDF not found: {input_path}")
    return p


def split_pdf_by_page_limit(input_path: str | Path, max_pages: int = 199) -> SplitResult:
    """Split a potentially large PDF and upload parts to GCS.

    Args:
        input_path: Local filesystem path to PDF (gs:// inputs not yet supported).
        max_pages: Maximum pages per split part.
    Returns:
        SplitResult with list of part gs:// URIs (ordered) and manifest URI.
    """
    if max_pages <= 0:
        raise ValueError("max_pages must be positive")

    local_path = _ensure_local_path(input_path)
    reader = PdfReader(str(local_path))
    total_pages = len(reader.pages)
    if total_pages <= max_pages:
        # No split required; we still return a manifest for consistency.
        _LOG.info(
            "split_not_required",
            extra={
                "pages": total_pages,
                "max_pages": max_pages,
                "split_forced": False,
                "estimated_page_count": total_pages,  # using actual when unsplit
                "actual_page_count": total_pages,
            },
        )
        gcs_client = storage.Client()
        split_id = uuid.uuid4().hex
        prefix = f"splits/{split_id}/"
        bucket = gcs_client.bucket(INTAKE_BUCKET)
        manifest = {
            "original_file": str(local_path),
            "total_pages": total_pages,
            "max_pages": max_pages,
            "estimated_page_count": total_pages,
            "parts": [
                {
                    "part": 1,
                    "name": local_path.name,
                    "page_start": 1,
                    "page_end": total_pages,
                    "gcs_uri": f"gs://{INTAKE_BUCKET}/{prefix}{local_path.name}",
                }
            ],
        }
        # Upload original file copy + manifest for traceability.
        # (Even though caller already has it locally, we keep consistent layout.)
        blob_pdf = bucket.blob(f"{prefix}{local_path.name}")
        blob_pdf.upload_from_filename(str(local_path))
        blob_manifest = bucket.blob(f"{prefix}manifest.json")
        blob_manifest.upload_from_string(json.dumps(manifest, indent=2), content_type="application/json")
        return SplitResult(parts=[manifest["parts"][0]["gcs_uri"]], manifest_gcs_uri=f"gs://{INTAKE_BUCKET}/{prefix}manifest.json")

    gcs_client = storage.Client()
    bucket = gcs_client.bucket(INTAKE_BUCKET)
    split_id = uuid.uuid4().hex
    prefix = f"splits/{split_id}/"

    parts: List[str] = []
    part_descriptors: List[dict] = []

    _LOG.info(
        "split_start",
        extra={
            "pages": total_pages,
            "max_pages": max_pages,
            "split_id": split_id,
            "split_forced": True,
            "estimated_page_count": total_pages,  # caller supplies only path so we compute actual
            "actual_page_count": total_pages,
        },
    )

    page_index = 0
    part_number = 1
    while page_index < total_pages:
        end_index = min(page_index + max_pages, total_pages)
        writer = PdfWriter()
        for i in range(page_index, end_index):
            writer.add_page(reader.pages[i])
        part_name = f"part_{part_number:04d}.pdf"
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            writer.write(tmp)
            tmp_path = Path(tmp.name)
        blob = bucket.blob(f"{prefix}{part_name}")
        blob.upload_from_filename(str(tmp_path))
        gcs_uri = f"gs://{INTAKE_BUCKET}/{prefix}{part_name}"
        parts.append(gcs_uri)
        part_descriptors.append(
            {
                "part": part_number,
                "name": part_name,
                "page_start": page_index + 1,
                "page_end": end_index,
                "gcs_uri": gcs_uri,
            }
        )
        _LOG.info(
            "split_upload",
            extra={
                "part": part_number,
                "pages_in_part": end_index - page_index,
                "page_start": page_index + 1,
                "page_end": end_index,
                "gcs_uri": gcs_uri,
            },
        )
        part_number += 1
        page_index = end_index

    manifest = {
        "original_file": str(local_path),
        "total_pages": total_pages,
        "max_pages": max_pages,
        "estimated_page_count": total_pages,
        "parts": part_descriptors,
    }
    blob_manifest = bucket.blob(f"{prefix}manifest.json")
    blob_manifest.upload_from_string(json.dumps(manifest, indent=2), content_type="application/json")
    manifest_gcs_uri = f"gs://{INTAKE_BUCKET}/{prefix}manifest.json"

    _LOG.info(
        "split_complete",
        extra={
            "parts": len(parts),
            "manifest": manifest_gcs_uri,
            "split_forced": True,
            "estimated_page_count": total_pages,
            "actual_page_count": total_pages,
        },
    )

    return SplitResult(parts=parts, manifest_gcs_uri=manifest_gcs_uri)


__all__ = ["split_pdf_by_page_limit", "SplitResult"]
