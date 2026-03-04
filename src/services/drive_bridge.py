"""Bridge helpers for polling Drive and mirroring PDFs into intake GCS."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from google.api_core import exceptions as gcloud_exceptions  # type: ignore
from google.cloud import storage  # type: ignore


class DriveDownloadClient(Protocol):
    """Minimal protocol expected from the Drive adapter used by FastAPI state."""

    def download_pdf(
        self,
        file_id: str,
        *,
        mime_type: str = "application/pdf",
        log_context: dict[str, Any] | None = None,
        quota_project: str | None = None,
        resource_key: str | None = None,
    ) -> bytes:
        """Download one Drive PDF by file id."""


@dataclass(frozen=True)
class DriveMirrorResult:
    drive_file_id: str
    object_uri: str
    created: bool


def build_intake_object_name(drive_file_id: str) -> str:
    token = drive_file_id.strip()
    if not token:
        raise ValueError("drive_file_id required")
    return f"uploads/drive/{token}.pdf"


def mirror_drive_pdf_to_intake(
    *,
    drive_client: DriveDownloadClient,
    intake_bucket: str,
    drive_file_id: str,
    source_folder_id: str,
    drive_shared_drive_id: str | None = None,
    file_name: str | None = None,
    resource_key: str | None = None,
    storage_client: storage.Client | None = None,
) -> DriveMirrorResult:
    """Download a Drive PDF and mirror it to intake GCS with idempotent semantics."""
    bucket_name = intake_bucket.strip()
    if not bucket_name:
        raise ValueError("intake_bucket required")
    folder_id = source_folder_id.strip()
    if not folder_id:
        raise ValueError("source_folder_id required")

    gcs_client = storage_client or storage.Client()
    object_name = build_intake_object_name(drive_file_id)
    bucket = gcs_client.bucket(bucket_name)
    blob = bucket.blob(object_name)

    # Skip already mirrored files before doing a Drive download.
    if blob.exists(client=gcs_client):
        return DriveMirrorResult(
            drive_file_id=drive_file_id,
            object_uri=f"gs://{bucket_name}/{object_name}",
            created=False,
        )

    pdf_bytes = drive_client.download_pdf(
        drive_file_id,
        log_context={"component": "drive_poll_bridge"},
        resource_key=resource_key,
    )
    metadata: dict[str, str] = {
        "source": "drive-poll",
        "drive_file_id": drive_file_id,
        "drive_input_folder_id": folder_id,
    }
    if file_name and file_name.strip():
        metadata["drive_file_name"] = file_name.strip()
    if drive_shared_drive_id and drive_shared_drive_id.strip():
        metadata["drive_shared_drive_id"] = drive_shared_drive_id.strip()
    blob.metadata = metadata

    try:
        blob.upload_from_string(
            pdf_bytes,
            content_type="application/pdf",
            if_generation_match=0,
        )
        created = True
    except gcloud_exceptions.PreconditionFailed:
        created = False

    return DriveMirrorResult(
        drive_file_id=drive_file_id,
        object_uri=f"gs://{bucket_name}/{object_name}",
        created=created,
    )


__all__ = ["DriveMirrorResult", "build_intake_object_name", "mirror_drive_pdf_to_intake"]
