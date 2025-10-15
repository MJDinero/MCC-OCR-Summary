import pytest

from src.models.events import OCRChunkMessage, StorageRequestMessage, SummaryResultMessage
from src.services.metrics import PrometheusMetrics
from src.services.storage_service import StorageConfig, StorageService
from src.services.summarization_service import (
    SummarisationConfig,
    SummarizationService,
)
from src.services.summary_store import InMemoryChunkSummaryStore


class DummyPublisher:
    def __init__(self) -> None:
        self.messages = []

    async def publish(self, topic: str, data: bytes, attributes: dict[str, str] | None = None) -> str:
        self.messages.append((topic, data, attributes or {}))
        return "msg"


class DummyLLM:
    async def summarize(self, *, prompt: str, text: str, temperature: float, max_output_tokens: int, model: str) -> str:
        return f"summary for {text[:10]}"


@pytest.mark.asyncio
async def test_summarization_service_metrics_flow():
    publisher = DummyPublisher()
    store = InMemoryChunkSummaryStore()
    service = SummarizationService(
        publisher=publisher,
        storage_topic="projects/demo/topics/storage",
        dlq_topic="projects/demo/topics/summariser-dlq",
        llm_client=DummyLLM(),
        store=store,
        config=SummarisationConfig(
            model_name="model",
            temperature=0.1,
            max_output_tokens=256,
            chunk_size=200,
            max_words=100,
        ),
        metrics=PrometheusMetrics(),
    )

    message = OCRChunkMessage(
        job_id="job-1",
        chunk_id="chunk-1",
        trace_id="trace-1",
        page_range=(1, 1),
        text="content",
        metadata={
            "chunk_index": "0",
            "is_last_chunk": "true",
            "total_chunks": "1",
            "source_uri": "gs://bucket/file.pdf",
        },
    )

    await service.handle_chunk(message)
    assert publisher.messages


class FailingStore(InMemoryChunkSummaryStore):
    async def write_chunk_summary(self, *, record: SummaryResultMessage) -> None:  # type: ignore[override]
        raise RuntimeError("store down")


@pytest.mark.asyncio
async def test_summarization_service_failure_sends_dlq():
    publisher = DummyPublisher()
    store = FailingStore()
    service = SummarizationService(
        publisher=publisher,
        storage_topic="projects/demo/topics/storage",
        dlq_topic="projects/demo/topics/summariser-dlq",
        llm_client=DummyLLM(),
        store=store,
        config=SummarisationConfig(
            model_name="model",
            temperature=0.1,
            max_output_tokens=256,
            chunk_size=200,
            max_words=100,
        ),
        metrics=PrometheusMetrics(),
    )

    message = OCRChunkMessage(
        job_id="job-err",
        chunk_id="chunk-err",
        trace_id="trace-err",
        page_range=(1, 1),
        text="content",
        metadata={"chunk_index": "0", "is_last_chunk": "true", "total_chunks": "1"},
    )

    with pytest.raises(RuntimeError):
        await service.handle_chunk(message)
    assert publisher.messages


class StubRepository:
    def __init__(self) -> None:
        self.calls = []

    def write_summary(self, **kwargs):  # type: ignore[override]
        self.calls.append(kwargs)


@pytest.mark.asyncio
async def test_storage_service_metrics_success():
    repository = StubRepository()
    publisher = DummyPublisher()
    service = StorageService(
        repository=repository,
        config=StorageConfig(
            output_bucket="summary",
            bigquery_dataset="dataset",
            bigquery_table="table",
            region="us",
        ),
        publisher=publisher,
        dlq_topic="projects/demo/topics/storage-dlq",
        metrics=PrometheusMetrics(),
    )

    message = StorageRequestMessage(
        job_id="job-2",
        trace_id="trace-2",
        final_summary="final text",
        per_chunk_summaries=[
            SummaryResultMessage(
                job_id="job-2",
                chunk_id="chunk-1",
                trace_id="trace-2",
                summary_text="chunk",
                section_index=0,
                total_sections=1,
            )
        ],
        object_uri="gs://bucket/file.pdf",
    )

    await service.handle_message(message)
    assert repository.calls


class FailingRepository(StubRepository):
    def write_summary(self, **kwargs):  # type: ignore[override]
        raise RuntimeError("db down")


@pytest.mark.asyncio
async def test_storage_service_failure_to_dlq():
    repository = FailingRepository()
    publisher = DummyPublisher()
    service = StorageService(
        repository=repository,
        config=StorageConfig(
            output_bucket="summary",
            bigquery_dataset="dataset",
            bigquery_table="table",
            region="us",
        ),
        publisher=publisher,
        dlq_topic="projects/demo/topics/storage-dlq",
        metrics=PrometheusMetrics(),
    )

    message = StorageRequestMessage(
        job_id="job-fail",
        trace_id="trace-fail",
        final_summary="final text",
        per_chunk_summaries=[],
        object_uri="gs://bucket/file.pdf",
    )

    with pytest.raises(RuntimeError):
        await service.handle_message(message)
    assert publisher.messages
