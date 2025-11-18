#!/usr/bin/env python3
"""Drive → ingest service synchroniser.

Polls a Google Drive folder for PDF files, uploads them to the intake GCS bucket,
and immediately registers the job through the public /ingest API so that all
pipelines share a single entrypoint. Structured logs and Prometheus counters
provide observability for operators running this utility on a schedule.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import time
from dataclasses import dataclass
from typing import Optional, Set

import requests
from googleapiclient.discovery import build  # type: ignore
from googleapiclient.http import MediaIoBaseDownload  # type: ignore
from google.oauth2 import service_account  # type: ignore

try:  # Optional dependency for storage uploads.
    from google.cloud import storage  # type: ignore
except Exception as exc:  # pragma: no cover - surface early
    raise SystemExit(f"google-cloud-storage unavailable: {exc}") from exc

try:  # Optional metrics
    from prometheus_client import Counter, Summary  # type: ignore
except Exception:  # pragma: no cover - allow running without metrics
    Counter = Summary = None  # type: ignore

from src.config import get_config
from src.utils.logging_utils import structured_log

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/devstorage.read_write",
]

LOG = logging.getLogger("drive_poller")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _mask_drive_id(file_id: str | None) -> str | None:
    if not file_id:
        return None
    token = file_id.strip()
    if len(token) <= 8:
        return token[:2] + "***"
    return f"{token[:4]}***{token[-4:]}"


if Counter is not None:  # pragma: no cover - metrics wiring
    POLLER_EVENTS = Counter(
        "drive_poller_events_total",
        "Drive poller events by outcome",
        ["event"],
    )
    INGEST_LATENCY = Summary(
        "drive_poller_ingest_latency_seconds",
        "Latency of ingest API calls",
    )
else:  # Lightweight shim when prometheus_client is absent

    class _NullMetric:
        def labels(self, *args, **kwargs):  # noqa: D401
            return self

        def inc(self, *_args, **_kwargs) -> None:  # noqa: D401
            return None

        def observe(self, *_args, **_kwargs) -> None:  # noqa: D401
            return None

    POLLER_EVENTS = _NullMetric()
    INGEST_LATENCY = _NullMetric()


@dataclass
class DrivePollerConfig:
    input_folder: str
    intake_bucket: str
    ingest_url: str
    prefix: Optional[str] = None
    interval: int = 60
    state_file: Optional[str] = None


class IngestPublisher:
    """Small helper around the /ingest endpoint."""

    def __init__(self, base_url: str, *, timeout: int = 30) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()

    def publish(
        self,
        *,
        bucket: str,
        object_name: str,
        drive_file_id: str,
    ) -> dict[str, object]:
        payload = {
            "object": {"bucket": bucket, "name": object_name},
            "source": "drive-poller",
            "driveFileId": drive_file_id,
            "attributes": {"driveFileId": drive_file_id},
        }
        url = f"{self._base_url}/ingest"
        start = time.perf_counter()
        try:
            response = self._session.post(url, json=payload, timeout=self._timeout)
            response.raise_for_status()
            result = response.json()
            INGEST_LATENCY.observe(time.perf_counter() - start)
            POLLER_EVENTS.labels(event="ingest_success").inc()
            return result
        except requests.RequestException as exc:  # pragma: no cover - network call
            POLLER_EVENTS.labels(event="ingest_failure").inc()
            raise RuntimeError(f"Ingest API call failed: {exc}") from exc


def _drive_service():  # pragma: no cover - external IO
    cfg = get_config()
    raw_credentials = cfg.google_application_credentials
    if not raw_credentials:
        raise SystemExit("GOOGLE_APPLICATION_CREDENTIALS must be configured")
    cred_source = raw_credentials.strip()
    subject = cfg.drive_impersonation_user
    if cred_source.startswith("{"):
        info = json.loads(cred_source)
        creds = service_account.Credentials.from_service_account_info(
            info,
            scopes=SCOPES,
            subject=subject,
        )  # type: ignore[arg-type]
    else:
        creds = service_account.Credentials.from_service_account_file(
            cred_source,
            scopes=SCOPES,
            subject=subject,
        )  # type: ignore[arg-type]
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _storage_client():  # pragma: no cover - external IO
    return storage.Client()


def _list_pdfs(service, folder_id: str):  # pragma: no cover - external IO
    q = (
        f"'{folder_id}' in parents and mimeType = 'application/pdf' "
        "and trashed = false"
    )
    resp = service.files().list(q=q, fields="files(id,name,modifiedTime)").execute()  # type: ignore[attr-defined]
    return resp.get("files", [])


def _load_state(path: str | None) -> Set[str]:
    if not path:
        return set()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return set(json.load(fh))
    except Exception:
        return set()


def _save_state(path: str | None, ids: Set[str]) -> None:
    if not path:
        return
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(sorted(ids), fh)
    try:
        import os

        os.replace(tmp, path)
    except OSError as exc:  # pragma: no cover - filesystem
        LOG.warning("state_file_replace_failed", extra={"error": str(exc)})


def _download_pdf(service, file_id: str) -> bytes:  # pragma: no cover
    request = service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()


def _upload_to_gcs(storage_client, bucket_name: str, object_name: str, data: bytes):
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    blob.upload_from_string(
        data,
        content_type="application/pdf",
        if_generation_match=0,
    )
    return f"gs://{bucket_name}/{object_name}"


class DrivePoller:
    def __init__(self, cfg: DrivePollerConfig) -> None:
        self.cfg = cfg
        self.drive = _drive_service()
        self.storage = _storage_client()
        self.publisher = IngestPublisher(cfg.ingest_url)
        self.processed = _load_state(cfg.state_file)

    def run(self) -> None:  # pragma: no cover - long running
        structured_log(
            LOG,
            logging.INFO,
            "drive_poller_start",
            folder_id=self.cfg.input_folder,
            interval=self.cfg.interval,
        )
        while True:
            try:
                self._poll_once()
            except KeyboardInterrupt:
                structured_log(LOG, logging.INFO, "drive_poller_stop")
                return
            except Exception as exc:  # noqa: BLE001
                structured_log(
                    LOG,
                    logging.ERROR,
                    "drive_poller_iteration_failed",
                    error=str(exc),
                )
                POLLER_EVENTS.labels(event="iteration_failure").inc()
            time.sleep(self.cfg.interval)

    def _poll_once(self) -> None:
        files = _list_pdfs(self.drive, self.cfg.input_folder)
        new_files = [entry for entry in files if entry["id"] not in self.processed]
        if not new_files:
            structured_log(LOG, logging.INFO, "drive_poller_idle", pending=len(files))
            return

        structured_log(
            LOG,
            logging.INFO,
            "drive_poller_new_files",
            count=len(new_files),
        )
        for entry in new_files:
            self._handle_entry(entry)
        _save_state(self.cfg.state_file, self.processed)

    def _handle_entry(self, entry: dict) -> None:
        file_id = entry["id"]
        masked = _mask_drive_id(file_id)
        name = entry.get("name") or file_id
        structured_log(
            LOG,
            logging.INFO,
            "drive_poller_processing",
            drive_file_id=masked,
            name=name,
        )
        try:
            pdf_bytes = _download_pdf(self.drive, file_id)
            prefix = (self.cfg.prefix.rstrip("/") + "/") if self.cfg.prefix else "intake/"
            object_name = f"{prefix}{file_id}.pdf"
            gcs_uri = _upload_to_gcs(
                self.storage, self.cfg.intake_bucket, object_name, pdf_bytes
            )
            ingest_resp = self.publisher.publish(
                bucket=self.cfg.intake_bucket,
                object_name=object_name,
                drive_file_id=file_id,
            )
            self.processed.add(file_id)
            structured_log(
                LOG,
                logging.INFO,
                "drive_poller_success",
                drive_file_id=masked,
                gcs_uri=gcs_uri,
                ingest_job=ingest_resp.get("job_id"),
            )
            POLLER_EVENTS.labels(event="file_success").inc()
        except Exception as exc:  # noqa: BLE001
            structured_log(
                LOG,
                logging.ERROR,
                "drive_poller_failure",
                drive_file_id=masked,
                error=str(exc),
            )
            POLLER_EVENTS.labels(event="file_failure").inc()


def parse_args():
    parser = argparse.ArgumentParser(description="Sync Drive PDFs to ingest API")
    parser.add_argument("--folder", help="Drive folder ID (defaults to AppConfig)")
    parser.add_argument("--intake-bucket", help="Intake GCS bucket (AppConfig default)")
    parser.add_argument("--ingest-url", required=True, help="Base URL for the ingest API")
    parser.add_argument("--prefix", help="GCS object prefix (default intake/)")
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Poll interval seconds (default 60)",
    )
    parser.add_argument("--state-file", help="Path to cache processed Drive IDs")
    return parser.parse_args()


def build_config(args) -> DrivePollerConfig:
    cfg = get_config()
    folder = args.folder or cfg.drive_input_folder_id
    bucket = args.intake_bucket or cfg.intake_gcs_bucket
    if not folder or not bucket:
        raise SystemExit("Folder ID and intake bucket are required")
    return DrivePollerConfig(
        input_folder=folder,
        intake_bucket=bucket,
        ingest_url=args.ingest_url,
        prefix=args.prefix,
        interval=args.interval,
        state_file=args.state_file,
    )


def main():
    args = parse_args()
    poller_cfg = build_config(args)
    poller = DrivePoller(poller_cfg)
    poller.run()


if __name__ == "__main__":  # pragma: no cover
    main()
