"""Chunk summary persistence implementations."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from typing import Dict, List

try:  # pragma: no cover - optional GCP dependencies
    from google.api_core import exceptions as gexc  # type: ignore
    from google.cloud import storage  # type: ignore
except Exception:  # pragma: no cover
    gexc = None  # type: ignore
    storage = None  # type: ignore

from src.config import get_config
from ..models.events import SummaryResultMessage
from .summarization_service import ChunkSummaryStore


class GCSChunkSummaryStore(ChunkSummaryStore):  # pragma: no cover - requires real GCS
    """Stores chunk summaries in a CMEK-protected GCS bucket."""

    def __init__(
        self,
        *,
        bucket_name: str,
        client: storage.Client | None = None,
        prefix: str = "summaries",
        kms_key_name: str | None = None,
    ) -> None:
        if storage is None:
            raise RuntimeError(
                "google-cloud-storage is required for GCSChunkSummaryStore"
            )
        self.client = client or storage.Client()
        self.bucket = self.client.bucket(bucket_name)
        self.prefix = prefix.rstrip("/")
        cfg = get_config()
        self.kms_key_name = kms_key_name or getattr(cfg, "cmek_key_name", None)

    async def write_chunk_summary(self, *, record: SummaryResultMessage) -> None:
        payload = asdict(record)

        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)

        conflict_exc = gexc.Conflict if gexc else Exception  # type: ignore[arg-type]

        def _upload() -> None:
            blob = self.bucket.blob(self._blob_name(record.job_id, record.chunk_id))
            if self.kms_key_name:
                setattr(blob, "kms_key_name", self.kms_key_name)
            blob.metadata = {
                key: str(value) for key, value in (record.metadata or {}).items()
            }
            try:
                blob.upload_from_string(
                    payload_json,
                    content_type="application/json",
                    if_generation_match=0,
                )
            except conflict_exc:  # type: ignore[misc]
                existing = blob.download_as_text()
                if existing != payload_json:
                    raise

        await asyncio.to_thread(_upload)

    async def list_chunk_summaries(self, *, job_id: str) -> list[SummaryResultMessage]:

        def _list() -> list[SummaryResultMessage]:
            prefix = f"{self.prefix}/{job_id}/chunks/"
            summaries: list[SummaryResultMessage] = []
            for blob in self.client.list_blobs(self.bucket, prefix=prefix):
                data = json.loads(blob.download_as_bytes())
                data["metadata"] = data.get("metadata") or {}
                summaries.append(SummaryResultMessage(**data))
            return summaries

        return await asyncio.to_thread(_list)

    def _blob_name(self, job_id: str, chunk_id: str) -> str:
        return f"{self.prefix}/{job_id}/chunks/{chunk_id}.json"


class InMemoryChunkSummaryStore(ChunkSummaryStore):
    """In-memory store for unit tests."""

    def __init__(self) -> None:
        self._store: Dict[str, Dict[str, SummaryResultMessage]] = {}

    async def write_chunk_summary(self, *, record: SummaryResultMessage) -> None:
        job_store = self._store.setdefault(record.job_id, {})
        job_store[record.chunk_id] = record

    async def list_chunk_summaries(self, *, job_id: str) -> List[SummaryResultMessage]:
        return list(self._store.get(job_id, {}).values())


__all__ = ["GCSChunkSummaryStore", "InMemoryChunkSummaryStore"]
