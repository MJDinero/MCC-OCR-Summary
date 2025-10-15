"""Repositories for persisting summarisation outputs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, Sequence

try:  # pragma: no cover - optional GCP dependencies
    from google.api_core import exceptions as gexc  # type: ignore
    from google.cloud import bigquery, storage  # type: ignore
except Exception:  # pragma: no cover
    gexc = None  # type: ignore
    bigquery = None  # type: ignore
    storage = None  # type: ignore

from ..models.events import SummaryResultMessage
from .storage_service import SummaryRepository


class HybridSummaryRepository(SummaryRepository):  # pragma: no cover - requires GCP services
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
    ) -> None:
        if bigquery is None or storage is None:
            raise RuntimeError("google-cloud-bigquery and google-cloud-storage are required")
        self.dataset = dataset
        self.table = table
        self.bucket_name = bucket_name
        self.region = region
        self.bq_client = bq_client or bigquery.Client()
        self.storage_client = storage_client or storage.Client()
        self.bucket = self.storage_client.bucket(bucket_name)

    def write_summary(
        self,
        *,
        job_id: str,
        final_summary: str,
        per_chunk_summaries: Sequence[SummaryResultMessage],
        metadata: dict[str, str],
    ) -> None:
        self._write_gcs(job_id, final_summary, per_chunk_summaries, metadata)
        self._write_bigquery(job_id, final_summary, per_chunk_summaries, metadata)

    def _write_gcs(
        self,
        job_id: str,
        final_summary: str,
        per_chunk_summaries: Sequence[SummaryResultMessage],
        metadata: dict[str, str],
    ) -> None:
        payload = {
            "job_id": job_id,
            "final_summary": final_summary,
            "per_chunk_summaries": [summary.summary_text for summary in per_chunk_summaries],
            "metadata": metadata,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        blob = self.bucket.blob(f"{job_id}/final_summary.json")
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
        final_summary: str,
        per_chunk_summaries: Sequence[SummaryResultMessage],
        metadata: dict[str, str],
    ) -> None:
        table_ref = f"{self.dataset}.{self.table}"
        rows = [
            {
                "job_id": job_id,
                "final_summary": final_summary,
                "chunk_summaries": [summary.summary_text for summary in per_chunk_summaries],
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


class InMemorySummaryRepository(SummaryRepository):
    """Simple repository for testing."""

    def __init__(self) -> None:
        self.records: Dict[str, dict] = {}

    def write_summary(
        self,
        *,
        job_id: str,
        final_summary: str,
        per_chunk_summaries: Sequence[SummaryResultMessage],
        metadata: dict[str, str],
    ) -> None:
        self.records[job_id] = {
            "final_summary": final_summary,
            "per_chunk_summaries": per_chunk_summaries,
            "metadata": metadata,
        }


__all__ = ["HybridSummaryRepository", "InMemorySummaryRepository"]
