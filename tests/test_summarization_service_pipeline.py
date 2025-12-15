import pytest

from src.models.events import OCRChunkMessage, StorageRequestMessage
from src.services.metrics import NullMetrics
from src.services.summarization_service import (
    SummarisationConfig,
    SummarizationService,
)
from src.services.summary_store import InMemoryChunkSummaryStore


class FakePublisher:
    def __init__(self) -> None:
        self.messages = []

    async def publish(
        self, topic: str, data: bytes, attributes: dict[str, str] | None = None
    ) -> str:
        self.messages.append((topic, data, attributes or {}))
        return "msg"


class StubLLM:
    async def summarize(
        self,
        *,
        prompt: str,
        text: str,
        temperature: float,
        max_output_tokens: int,
        model: str,
    ) -> str:
        # Return first 10 words to keep deterministic
        words = text.split()
        summary = " ".join(words[: min(10, len(words))])
        return f"summary: {summary}" if summary else "summary: (empty)"


@pytest.mark.asyncio
async def test_summarization_service_produces_final_message():
    publisher = FakePublisher()
    store = InMemoryChunkSummaryStore()
    service = SummarizationService(
        publisher=publisher,
        storage_topic="projects/demo/topics/storage",
        dlq_topic="projects/demo/topics/summarization-dlq",
        llm_client=StubLLM(),
        store=store,
        config=SummarisationConfig(
            model_name="test-model",
            temperature=0.1,
            max_output_tokens=256,
            chunk_size=400,
            max_words=120,
        ),
        metrics=NullMetrics(),
    )

    chunk1 = OCRChunkMessage(
        job_id="job-123",
        chunk_id="chunk-0",
        trace_id="trace-1",
        page_range=(1, 2),
        text=" ".join(f"Sentence {i}" for i in range(100)),
        metadata={
            "chunk_index": "0",
            "is_last_chunk": "false",
            "source_uri": "gs://bucket/document.pdf",
        },
    )
    chunk2 = OCRChunkMessage(
        job_id="job-123",
        chunk_id="chunk-1",
        trace_id="trace-1",
        page_range=(3, 4),
        text=" ".join(f"Another sentence {i}" for i in range(120)),
        metadata={
            "chunk_index": "1",
            "is_last_chunk": "true",
            "total_chunks": "2",
            "source_uri": "gs://bucket/document.pdf",
        },
    )

    await service.handle_chunk(chunk1)
    await service.handle_chunk(chunk2)

    assert len(publisher.messages) == 1
    topic, data, attributes = publisher.messages[0]
    assert topic.endswith("/topics/storage")
    storage_message = StorageRequestMessage.from_pubsub(data, attributes)
    assert storage_message.job_id == "job-123"
    assert storage_message.per_chunk_summaries
    assert len(storage_message.per_chunk_summaries) == 2
    assert "sections" in storage_message.final_summary
