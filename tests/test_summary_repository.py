from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.models.events import SummaryResultMessage
from src.services import summary_repository


class FakeBlob:
    def __init__(self) -> None:
        self.uploaded_data: str | None = None
        self.metadata: dict[str, str] | None = None
        self.kms_key_name: str | None = None

    def upload_from_string(
        self, data: str, *, content_type: str, if_generation_match: int
    ) -> None:
        self.uploaded_data = data
        self._content_type = content_type
        self._if_generation_match = if_generation_match

    def download_as_text(self) -> str:
        return self.uploaded_data or ""


class FakeBucket:
    def __init__(self) -> None:
        self.blobs: dict[str, FakeBlob] = {}

    def blob(self, name: str) -> FakeBlob:
        blob = self.blobs.get(name)
        if blob is None:
            blob = FakeBlob()
            self.blobs[name] = blob
        return blob


class FakeStorageClient:
    def __init__(self, bucket: FakeBucket) -> None:
        self.bucket_name: str | None = None
        self._bucket = bucket

    def bucket(self, name: str) -> FakeBucket:
        self.bucket_name = name
        return self._bucket


class FakeBigQueryClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict[str, object]], list[str]]] = []

    def insert_rows_json(
        self, table_ref: str, rows: list[dict[str, object]], row_ids: list[str]
    ) -> list:
        self.calls.append((table_ref, rows, row_ids))
        self.last_rows = rows
        return []


@pytest.fixture(autouse=True)
def _deterministic_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        summary_repository,
        "get_config",
        lambda: SimpleNamespace(cmek_key_name=None),
    )


def test_hybrid_repo_serializes_final_summary() -> None:
    bucket = FakeBucket()
    storage_client = FakeStorageClient(bucket)
    bigquery_client = FakeBigQueryClient()
    repo = summary_repository.HybridSummaryRepository(
        dataset="ds",
        table="tbl",
        bucket_name="bucket",
        region="us",
        bq_client=bigquery_client,
        storage_client=storage_client,
        kms_key_name=None,
    )
    final_summary = {
        "schema_version": "test-v1",
        "sections": [{"title": "Reason for Visit", "content": "Routine"}],
        "_claims": [{"claim_id": "c1", "section": "Reason for Visit", "value": "Routine"}],
        "_evidence_spans": [],
        "metadata": {"source": "unit-test"},
    }
    per_chunk = [
        SummaryResultMessage(
            job_id="job-123",
            chunk_id="chunk-1",
            trace_id="trace",
            summary_text="chunk summary",
            section_index=0,
            total_sections=1,
        )
    ]
    repo.write_summary(
        job_id="job-123",
        final_summary=final_summary,
        per_chunk_summaries=per_chunk,
        metadata={"doc": "abc"},
    )

    blob = bucket.blobs["job-123/final_summary.json"]
    assert blob.uploaded_data is not None
    payload = json.loads(blob.uploaded_data)
    expected_summary = json.dumps(final_summary, separators=(",", ":"), sort_keys=True)
    assert isinstance(payload["final_summary"], str)
    assert payload["final_summary"] == expected_summary
    assert payload["per_chunk_summaries"] == ["chunk summary"]

    table_ref, rows, row_ids = bigquery_client.calls[-1]
    assert table_ref == "ds.tbl"
    assert row_ids == ["job-123"]
    assert rows[0]["final_summary"] == expected_summary
