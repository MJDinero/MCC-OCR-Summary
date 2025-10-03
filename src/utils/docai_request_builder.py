from __future__ import annotations
"""Pure helper for constructing Document AI process_document request payloads.

Extracted from `mcc.services.docai_helper.process_document` to allow offline
unit testing without requiring Google client imports.
"""
from pathlib import Path
from typing import Tuple


def build_docai_request(file_path: str, project_id: str, location: str, processor_id: str) -> Tuple[str, dict]:
    """Return (name, request_dict) for DocumentProcessorServiceClient.process_document.

    Args:
        file_path: Path to PDF file.
        project_id: GCP project id.
        location: DocAI location (e.g. us or us-central1).
        processor_id: Processor identifier.
    Returns:
        tuple: (resource_name, request_dict)
    Raises:
        FileNotFoundError: if file not found
        ValueError: if any id components empty
    """
    if not project_id or not location or not processor_id:
        raise ValueError("project_id, location, and processor_id are required")
    file_bytes = Path(file_path).read_bytes()
    name = f"projects/{project_id}/locations/{location}/processors/{processor_id}"
    document = {"content": file_bytes, "mime_type": "application/pdf"}
    return name, {"name": name, "raw_document": document}

__all__ = ["build_docai_request"]
