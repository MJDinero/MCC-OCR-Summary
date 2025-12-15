"""Repositories for persisting summarisation outputs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Sequence

try:  # pragma: no cover - optional GCP dependencies
    from google.api_core import exceptions as gexc  # type: ignore
    from google.cloud import bigquery, storage  # type: ignore
except Exception:  # pragma: no cover
    gexc = None  # type: ignore
    bigquery = None  # type: ignore
    storage = None  # type: ignore

from src.config import get_config
from ..models.events import SummaryResultMessage
from .storage_service import SummaryRepository


class HybridSummaryRepository(
    SummaryRepository
):  # pragma: no cover - requires GCP services
    """Stores summaries in both BigQuery and GCS (idempotent)."""

    def __init__(
        self,
        *,
        dataset: str,
        table: str,
        bucket_name: str,
        region: str,
        bq_client: bigquery.Client | None = None,
        storage_client: storage.Client | None = None,
        kms_key_name: str | None = None,
    ) -> None:
        if bigquery is None or storage is None:
            raise RuntimeError(
                "google-cloud-bigquery and google-cloud-storage are required"
            )
        self.dataset = dataset
        self.table = table
        self.bucket_name = bucket_name
        self.region = region
        self.bq_client = bq_client or bigquery.Client()
        self.storage_client = storage_client or storage.Client()
        self.bucket = self.storage_client.bucket(bucket_name)
        cfg = get_config()
        self.kms_key_name = kms_key_name or getattr(cfg, "cmek_key_name", None)
        self._table_encryption_validated = False

    def write_summary(
        self,
        *,
        job_id: str,
        final_summary: Mapping[str, Any],
        per_chunk_summaries: Sequence[SummaryResultMessage],
        metadata: dict[str, str],
    ) -> None:
        self._write_gcs(job_id, final_summary, per_chunk_summaries, metadata)
        self._write_bigquery(job_id, final_summary, per_chunk_summaries, metadata)

    def _write_gcs(
        self,
        job_id: str,
        final_summary: Mapping[str, Any],
        per_chunk_summaries: Sequence[SummaryResultMessage],
        metadata: dict[str, str],
    ) -> None:
        payload = {
            "job_id": job_id,
            "final_summary": final_summary,
            "per_chunk_summaries": [
                summary.summary_text for summary in per_chunk_summaries
            ],
            "metadata": metadata,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        blob = self.bucket.blob(f"{job_id}/final_summary.json")
        if self.kms_key_name:
            setattr(blob, "kms_key_name", self.kms_key_name)
        blob.metadata = metadata
        conflict_exc = gexc.Conflict if gexc else Exception  # type: ignore[arg-type]
        try:
            blob.upload_from_string(
                json.dumps(payload, separators=(",", ":"), sort_keys=True),
                content_type="application/json",
                if_generation_match=0,
            )
        except conflict_exc:  # type: ignore[misc]
            existing = blob.download_as_text()
            if json.loads(existing) != payload:
                raise

    def _write_bigquery(
        self,
        job_id: str,
        final_summary: Mapping[str, Any],
        per_chunk_summaries: Sequence[SummaryResultMessage],
        metadata: dict[str, str],
    ) -> None:
        table_ref = f"{self.dataset}.{self.table}"
        self._ensure_table_encryption(table_ref)
        rows = [
            {
                "job_id": job_id,
                "final_summary": final_summary,
                "chunk_summaries": [
                    summary.summary_text for summary in per_chunk_summaries
                ],
                "metadata": metadata,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ]
        errors = self.bq_client.insert_rows_json(
            table_ref,
            rows,
            row_ids=[job_id],
        )
        if errors:
            raise RuntimeError(f"Failed to insert summary rows: {errors}")

    def _ensure_table_encryption(self, table_ref: str) -> None:
        if not self.kms_key_name or self._table_encryption_validated:
            return
        try:
            table = self.bq_client.get_table(table_ref)
        except Exception as exc:  # pragma: no cover - network call
            raise RuntimeError(
                f"Unable to verify encryption for {table_ref}: {exc}"
            ) from exc
        encryption_cfg = getattr(table, "encryption_configuration", None)
        kms_key = (
            getattr(encryption_cfg, "kms_key_name", None) if encryption_cfg else None
        )
        if kms_key != self.kms_key_name:
            raise RuntimeError(
                f"BigQuery table {table_ref} is not encrypted with expected CMEK {self.kms_key_name}"
            )
        self._table_encryption_validated = True


class InMemorySummaryRepository(SummaryRepository):
    """Simple repository for testing."""

    def __init__(self) -> None:
        self.records: Dict[str, dict] = {}

    def write_summary(
        self,
        *,
        job_id: str,
        final_summary: Mapping[str, Any],
        per_chunk_summaries: Sequence[SummaryResultMessage],
        metadata: dict[str, str],
    ) -> None:
        self.records[job_id] = {
            "final_summary": final_summary,
            "per_chunk_summaries": per_chunk_summaries,
            "metadata": metadata,
        }


__all__ = ["HybridSummaryRepository", "InMemorySummaryRepository"]
