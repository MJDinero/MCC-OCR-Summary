import json
import logging

import pytest

from src.models.events import StorageRequestMessage, SummaryResultMessage
from src.services.metrics import NullMetrics
from src.services.storage_service import StorageConfig, StorageService
from src.services.summary_repository import InMemorySummaryRepository


class FakePublisher:
    def __init__(self) -> None:
        self.messages = []

    async def publish(
        self, topic: str, data: bytes, attributes: dict[str, str] | None = None
    ) -> str:
        self.messages.append((topic, data, attributes or {}))
        return "msg"


@pytest.mark.asyncio
async def test_storage_service_persists_summary():
    repository = InMemorySummaryRepository()
    publisher = FakePublisher()
    service = StorageService(
        repository=repository,
        config=StorageConfig(
            output_bucket="summary-bucket",
            bigquery_dataset="dataset",
            bigquery_table="table",
            region="us-central1",
        ),
        publisher=publisher,
        dlq_topic="projects/demo/topics/storage-dlq",
        metrics=NullMetrics(),
    )
    message = StorageRequestMessage(
        job_id="job-1",
        trace_id="trace-1",
        final_summary={"schema_version": "test", "sections": []},
        per_chunk_summaries=[
            SummaryResultMessage(
                job_id="job-1",
                chunk_id="chunk-1",
                trace_id="trace-1",
                summary_text="chunk summary",
                section_index=0,
                total_sections=1,
            )
        ],
        object_uri="gs://bucket/document.pdf",
    )
    await service.handle_message(message)
    assert "job-1" in repository.records
    assert not publisher.messages


class FailingRepository(InMemorySummaryRepository):
    def write_summary(self, **kwargs):  # type: ignore[override]
        raise RuntimeError("db down")


class SensitiveFailingRepository(InMemorySummaryRepository):
    def write_summary(self, **kwargs):  # type: ignore[override]
        raise RuntimeError(
            "write failed for jane.doe@example.com with SSN 123-45-6789 in patient note"
        )


@pytest.mark.asyncio
async def test_storage_service_sends_to_dlq_on_failure():
    repository = FailingRepository()
    publisher = FakePublisher()
    service = StorageService(
        repository=repository,
        config=StorageConfig(
            output_bucket="summary",
            bigquery_dataset="dataset",
            bigquery_table="table",
            region="us-central1",
        ),
        publisher=publisher,
        dlq_topic="projects/demo/topics/storage-dlq",
        metrics=NullMetrics(),
    )
    message = StorageRequestMessage(
        job_id="job-2",
        trace_id="trace-2",
        final_summary={"schema_version": "test", "sections": []},
        per_chunk_summaries=[],
        object_uri="gs://bucket/doc.pdf",
    )
    with pytest.raises(RuntimeError):
        await service.handle_message(message)
    assert publisher.messages


@pytest.mark.asyncio
async def test_storage_service_redacts_failure_error_for_logs_and_dlq(caplog):
    repository = SensitiveFailingRepository()
    publisher = FakePublisher()
    service = StorageService(
        repository=repository,
        config=StorageConfig(
            output_bucket="summary",
            bigquery_dataset="dataset",
            bigquery_table="table",
            region="us-central1",
        ),
        publisher=publisher,
        dlq_topic="projects/demo/topics/storage-dlq",
        metrics=NullMetrics(),
    )
    message = StorageRequestMessage(
        job_id="job-3",
        trace_id="trace-3",
        final_summary={"schema_version": "test", "sections": []},
        per_chunk_summaries=[],
        object_uri="gs://bucket/doc.pdf",
    )

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError):
            await service.handle_message(message)

    assert publisher.messages
    _topic, data, _attrs = publisher.messages[0]
    payload = json.loads(data.decode("utf-8"))

    assert payload["redaction_applied"] is True
    assert "[REDACTED]" in payload["error_message"]
    assert "jane.doe@example.com" not in payload["error_message"]
    assert "123-45-6789" not in payload["error_message"]

    assert "storage_failed" in caplog.text
    assert "jane.doe@example.com" not in caplog.text
    assert "123-45-6789" not in caplog.text
