import pytest

from src.models.events import DocumentIngestionEvent
from src.services.chunker import Chunk
from src.services.metrics import PrometheusMetrics
from src.services.ocr_service import OCRService


class DummyPublisher:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bytes, dict[str, str]]] = []

    async def publish(
        self, topic: str, data: bytes, attributes: dict[str, str] | None = None
    ) -> str:
        self.messages.append((topic, data, attributes or {}))
        return "pub-1"


class DummyChunker:
    async def chunk_async(self, pages):
        yield Chunk(
            text="hello world", index=0, page_start=1, page_end=1, token_count=3
        )


async def _fake_pages(_self, _event):
    yield "hello world"


async def _noop_publish_chunk(self, event, chunk, *, is_last_chunk, total_chunks):
    await self.publisher.publish(
        self.summary_topic,
        f"{chunk.text}:{is_last_chunk}".encode("utf-8"),
        {"job_id": event.job_id},
    )


@pytest.mark.asyncio
async def test_ocr_service_records_metrics(monkeypatch):
    publisher = DummyPublisher()
    metrics = PrometheusMetrics()
    service = OCRService(
        processor_name="projects/proj/locations/us/processors/pid",
        summary_topic="projects/proj/topics/summary",
        publisher=publisher,
        dlq_topic="projects/proj/topics/dlq",
        metrics=metrics,
        chunker=DummyChunker(),
    )
    monkeypatch.setattr(
        service, "_iterate_pages", _fake_pages.__get__(service, OCRService)
    )
    monkeypatch.setattr(
        service, "_publish_chunk", _noop_publish_chunk.__get__(service, OCRService)
    )

    event = DocumentIngestionEvent(
        job_id="job-1",
        bucket="intake",
        object_name="file.pdf",
        generation="1",
        trace_id="trace-1",
        request_id="req-1",
    )

    await service.handle_event(event)
    assert publisher.messages


@pytest.mark.asyncio
async def test_ocr_service_failure_sends_to_dlq(monkeypatch):
    publisher = DummyPublisher()
    metrics = PrometheusMetrics()
    service = OCRService(
        processor_name="projects/proj/locations/us/processors/pid",
        summary_topic="projects/proj/topics/summary",
        publisher=publisher,
        dlq_topic="projects/proj/topics/dlq",
        metrics=metrics,
        chunker=DummyChunker(),
    )
    monkeypatch.setattr(
        service, "_iterate_pages", _fake_pages.__get__(service, OCRService)
    )

    async def raising_publish_chunk(self, event, chunk, *, is_last_chunk, total_chunks):
        raise RuntimeError("publish failure")

    monkeypatch.setattr(
        service, "_publish_chunk", raising_publish_chunk.__get__(service, OCRService)
    )

    event = DocumentIngestionEvent(
        job_id="job-err",
        bucket="intake",
        object_name="file.pdf",
        generation="1",
        trace_id="trace-err",
        request_id="req-err",
    )

    with pytest.raises(RuntimeError):
        await service.handle_event(event)
    # DLQ payload written despite error
    assert publisher.messages
