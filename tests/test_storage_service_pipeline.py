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
        final_summary="The final summary text.",
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
        final_summary="Final summary",
        per_chunk_summaries=[],
        object_uri="gs://bucket/doc.pdf",
    )
    with pytest.raises(RuntimeError):
        await service.handle_message(message)
    assert publisher.messages
