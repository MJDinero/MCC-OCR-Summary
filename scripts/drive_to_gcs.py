#!/usr/bin/env python3
"""Drive → GCS intake synchroniser.

Polls a Google Drive folder for new PDF files and uploads them to the intake
GCS bucket. The asynchronous pipeline is triggered by Eventarc on the ensuing
GCS finalize events, so this utility performs *no* HTTP calls to the API
service. Idempotency is enforced via optional state tracking and
``ifGenerationMatch=0`` writes.

Usage::

    python scripts/drive_to_gcs.py --folder <drive-folder-id> --intake-bucket <gcs-bucket>

Recommended flags:
  --interval    Poll interval seconds (default 60)
  --state-file  Persist processed Drive IDs to disk to avoid duplicates
  --prefix      Optional GCS prefix (defaults to intake/<drive-file-id>)

Required environment variables:
  GOOGLE_APPLICATION_CREDENTIALS (service account with Drive reader + Storage writer)

"""
from __future__ import annotations

import argparse
import io
import json
import os
import time
from dataclasses import dataclass
from typing import Optional, Set

from googleapiclient.discovery import build  # type: ignore
from googleapiclient.http import MediaIoBaseDownload  # type: ignore
from google.oauth2 import service_account  # type: ignore

try:  # optional dependency in some test environments
    from google.cloud import storage  # type: ignore
except Exception as exc:  # pragma: no cover - surface early for operators
    raise SystemExit(f"google-cloud-storage unavailable: {exc}")


SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/devstorage.read_write",
]


@dataclass
class DrivePollerConfig:
    input_folder: str
    intake_bucket: str
    prefix: Optional[str] = None
    interval: int = 60
    state_file: Optional[str] = None


def _drive_service():  # pragma: no cover - external IO
    gac = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not gac or not os.path.exists(gac):
        raise SystemExit("GOOGLE_APPLICATION_CREDENTIALS not set or file missing")
    creds = service_account.Credentials.from_service_account_file(gac, scopes=SCOPES)  # type: ignore[arg-type]
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _storage_client():  # pragma: no cover - external IO
    return storage.Client()


def _list_pdfs(service, folder_id: str):  # pragma: no cover - external IO
    q = f"'{folder_id}' in parents and mimeType = 'application/pdf' and trashed = false"
    resp = service.files().list(q=q, fields="files(id,name,modifiedTime)").execute()  # type: ignore[attr-defined]
    return resp.get("files", [])


def _load_state(path: str | None) -> Set[str]:
    if not path or not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return set(json.load(fh))
    except Exception:
        return set()


def _save_state(path: str | None, ids: Set[str]):  # pragma: no cover - file IO
    if not path:
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(sorted(ids), fh)
    os.replace(tmp, path)


def _download_pdf(service, file_id: str) -> bytes:  # pragma: no cover - external IO
    request = service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
        if status:
            print(f"[drive→gcs] Downloading {file_id}: {int(status.progress() * 100)}%")
    return buffer.getvalue()


def _upload_to_gcs(storage_client, bucket_name: str, object_name: str, data: bytes) -> str:
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    blob.upload_from_string(
        data,
        content_type="application/pdf",
        if_generation_match=0,
    )
    return f"gs://{bucket_name}/{object_name}"


def poll_loop(cfg: DrivePollerConfig):  # pragma: no cover - long-running loop
    drive = _drive_service()
    storage_client = _storage_client()
    processed = _load_state(cfg.state_file)
    print(f"[drive→gcs] Starting poll loop. Cached IDs: {len(processed)}")
    while True:
        try:
            files = _list_pdfs(drive, cfg.input_folder)
            new_files = [f for f in files if f["id"] not in processed]
            if new_files:
                print(f"[drive→gcs] Found {len(new_files)} new PDF(s)")
            for entry in new_files:
                fid = entry["id"]
                name = entry.get("name") or fid
                print(f"[drive→gcs] Processing {fid} ({name})")
                try:
                    pdf_bytes = _download_pdf(drive, fid)
                    prefix = cfg.prefix.rstrip("/") + "/" if cfg.prefix else "intake/"
                    object_name = f"{prefix}{fid}.pdf"
                    gcs_uri = _upload_to_gcs(storage_client, cfg.intake_bucket, object_name, pdf_bytes)
                    processed.add(fid)
                    print(f"[drive→gcs] Uploaded to {gcs_uri}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[drive→gcs] Error processing {fid}: {exc}")
            if new_files:
                _save_state(cfg.state_file, processed)
            time.sleep(cfg.interval)
        except KeyboardInterrupt:
            print("[drive→gcs] Exiting on Ctrl+C")
            break
        except Exception as exc:  # noqa: BLE001
            print(f"[drive→gcs] Loop error: {exc}; sleeping {cfg.interval}s")
            time.sleep(cfg.interval)


def main():  # pragma: no cover
    ap = argparse.ArgumentParser(description="Sync Drive PDFs to GCS intake bucket")
    ap.add_argument("--folder", default=os.environ.get("DRIVE_INPUT_FOLDER_ID"))
    ap.add_argument("--intake-bucket", default=os.environ.get("INTAKE_GCS_BUCKET"))
    ap.add_argument("--prefix", default=os.environ.get("INTAKE_PREFIX"))
    ap.add_argument("--interval", type=int, default=int(os.environ.get("POLL_INTERVAL", "60")))
    ap.add_argument("--state-file", default=os.environ.get("STATE_FILE"))
    args = ap.parse_args()
    if not args.folder:
        ap.error("--folder or DRIVE_INPUT_FOLDER_ID required")
    if not args.intake_bucket:
        ap.error("--intake-bucket or INTAKE_GCS_BUCKET required")
    cfg = DrivePollerConfig(
        input_folder=args.folder,
        intake_bucket=args.intake_bucket,
        prefix=args.prefix,
        interval=args.interval,
        state_file=args.state_file,
    )
    poll_loop(cfg)


if __name__ == "__main__":  # pragma: no cover
    main()
