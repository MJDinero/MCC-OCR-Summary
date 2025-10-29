"""Pure helper for constructing Document AI process_document request payloads.

Extracted from `mcc.services.docai_helper.process_document` to allow offline
unit testing without requiring Google client imports.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Optional, Tuple, Union

from src.errors import ValidationError

DEFAULT_MAX_PDF_BYTES = 80 * 1024 * 1024  # 80MB default; can be overridden via config
# Backward-compatible alias used in earlier tests/code
MAX_PDF_BYTES = DEFAULT_MAX_PDF_BYTES
_PDF_MAGIC = b"%PDF-"  # simple magic header check


def _validate_ids(project_id: str, location: str, processor_id: str) -> None:
    if not all([project_id, location, processor_id]):
        raise ValidationError("project_id, location, and processor_id are required")


def _load_bytes(file_path: Path) -> bytes:
    if not file_path.exists():
        raise ValidationError(f"File not found: {file_path}")
    if file_path.suffix.lower() != ".pdf":
        raise ValidationError("Only PDF files are supported (.pdf extension required)")
    data = file_path.read_bytes()
    return data


def _validate_pdf_bytes(data: bytes, max_bytes: int) -> None:
    if not data:
        raise ValidationError("Empty file bytes")
    if len(data) > max_bytes:
        raise ValidationError(f"PDF exceeds max size of {max_bytes} bytes")
    if not data.startswith(_PDF_MAGIC):
        # A very small subset of PDFs may have comments before %PDF- but we
        # intentionally enforce strictness for user uploads.
        raise ValidationError("File does not appear to be a valid PDF (missing %PDF- header)")


def build_docai_request(
    file_source: Union[str, bytes, Path],
    project_id: str,
    location: str,
    processor_id: str,
    *,
    filename: Optional[str] = None,
    legacy_layout: bool = False,
    enable_image_quality_scores: bool = True,
) -> Tuple[str, dict]:
    """Return (resource_name, request_dict) for DocumentProcessorServiceClient.process_document.

    Supports either a file path (str or Path) or raw PDF bytes. Performs strict
    validation (extension, magic header, size) to fail fast before incurring
    remote API cost.

    Args:
        file_source: Path to PDF or raw bytes.
        project_id: GCP project id.
        location: DocAI location (e.g. 'us').
        processor_id: Processor identifier.
        filename: Optional original filename (used for mimetype inference when bytes provided).
    Returns:
        (resource_name, request_dict)
    Raises:
        ValidationError: on any validation failure.
    """
    _validate_ids(project_id, location, processor_id)

    if isinstance(file_source, (str, Path)):
        path = Path(file_source)
        data = _load_bytes(path)
        inferred_name = path.name
    elif isinstance(file_source, (bytes, bytearray)):
        if filename and Path(filename).suffix.lower() != ".pdf":
            raise ValidationError("Provided filename must end with .pdf for bytes input")
        data = bytes(file_source)
        inferred_name = filename or "upload.pdf"
    else:
        raise ValidationError("file_source must be path-like or bytes")

    max_bytes = DEFAULT_MAX_PDF_BYTES  # local default; callers may post-process or patch if needed
    _validate_pdf_bytes(data, max_bytes)

    # For completeness, allow mimetypes to guess; fallback to application/pdf
    mime_type = mimetypes.guess_type(inferred_name)[0] or "application/pdf"
    if mime_type != "application/pdf":  # should not happen due to .pdf enforcement
        raise ValidationError("Invalid mime type; expected application/pdf")

    resource_name = f"projects/{project_id}/locations/{location}/processors/{processor_id}"
    request = {
        "name": resource_name,
        "raw_document": {"content": data, "mime_type": "application/pdf"},
    }

    ocr_config: dict[str, Any] = {}
    advanced: list[str] = []
    if legacy_layout:
        advanced.append("legacy_layout")
    if advanced:
        ocr_config["advanced_ocr_options"] = advanced
    if enable_image_quality_scores:
        ocr_config["enable_image_quality_scores"] = True
    if ocr_config:
        request["process_options"] = {"ocr_config": ocr_config}

    return resource_name, request


__all__ = ["build_docai_request", "DEFAULT_MAX_PDF_BYTES", "MAX_PDF_BYTES"]
