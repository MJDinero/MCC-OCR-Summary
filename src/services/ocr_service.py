"""OCR service: streams Document AI output and publishes summarisation chunks."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from typing import AsyncIterator

from google.cloud import documentai_v1 as documentai  # type: ignore
from tenacity import AsyncRetrying, stop_after_attempt, wait_random_exponential

from ..models.events import DocumentIngestionEvent, OCRChunkMessage
from ..utils.redact import redact_mapping
from .chunker import Chunk, Chunker
from .interfaces import MetricsClient, PubSubPublisher

LOG = logging.getLogger(__name__)


class OCRService:
    """Coordinates OCR processing and chunk publication."""

    def __init__(
        self,
        *,
        processor_name: str,
        summary_topic: str,
        publisher: PubSubPublisher,
        dlq_topic: str,
        metrics: MetricsClient | None = None,
        chunker: Chunker | None = None,
        docai_client: documentai.DocumentProcessorServiceAsyncClient | None = None,
    ) -> None:
        self.processor_name = processor_name
        self.summary_topic = summary_topic
        self.publisher = publisher
        self.dlq_topic = dlq_topic
        self.metrics = metrics
        self.chunker = chunker or Chunker()
        self._docai_client = docai_client or documentai.DocumentProcessorServiceAsyncClient()

    async def handle_event(self, event: DocumentIngestionEvent) -> None:
        """Entry point for Pub/Sub triggered OCR execution."""
        start = time.perf_counter()
        LOG.info(
            "ocr_service_start",
            extra={"job_id": event.job_id, "trace_id": event.trace_id, "bucket": event.bucket},
        )
        try:
            pages = self._iterate_pages(event)
            previous: Chunk | None = None
            chunk_count = 0
            async for chunk in self._chunk_document(pages):
                if previous is not None:
                    await self._publish_chunk(event, previous, is_last_chunk=False, total_chunks=None)
                previous = chunk
                chunk_count += 1
            if previous is not None:
                await self._publish_chunk(event, previous, is_last_chunk=True, total_chunks=chunk_count)
            duration = time.perf_counter() - start
            if self.metrics:
                self.metrics.observe_latency(
                    "ocr_latency_seconds",
                    duration,
                    stage="ocr",
                )
            LOG.info(
                "ocr_service_completed",
                extra={
                    "job_id": event.job_id,
                    "trace_id": event.trace_id,
                    "duration_seconds": duration,
                },
            )
        except Exception as exc:  # noqa: BLE001 - propagate to DLQ
            await self._handle_failure(event, exc)
            raise

    async def _chunk_document(self, pages: AsyncIterator[str]) -> AsyncIterator[Chunk]:
        async for chunk in self.chunker.chunk_async(pages):
            yield chunk

    async def _publish_chunk(
        self,
        event: DocumentIngestionEvent,
        chunk: Chunk,
        *,
        is_last_chunk: bool,
        total_chunks: int | None,
    ) -> None:
        message = OCRChunkMessage(
            job_id=event.job_id,
            chunk_id=f"{event.job_id}-{chunk.index}",
            trace_id=event.trace_id,
            page_range=(chunk.page_start, chunk.page_end),
            text=chunk.text,
            shard_id=event.attributes.get("shard_id") if event.attributes else None,
            source_event_id=event.attributes.get("message_id") if event.attributes else None,
            metadata={
                "chunk_index": str(chunk.index),
                "token_count": str(chunk.token_count),
                "is_last_chunk": str(is_last_chunk).lower(),
                **({"total_chunks": str(total_chunks)} if total_chunks is not None else {}),
                "source_uri": event.gcs_uri or f"gs://{event.bucket}/{event.object_name}",
            },
        )
        data, attributes = message.to_pubsub()
        await self.publisher.publish(self.summary_topic, data, attributes)

    async def _handle_failure(self, event: DocumentIngestionEvent, error: Exception) -> None:
        LOG.exception(
            "ocr_service_failed",
            extra={"job_id": event.job_id, "trace_id": event.trace_id},
        )
        payload = {
            "event": redact_mapping(asdict(event)),
            "error": {
                "type": type(error).__name__,
                "message": str(error),
            },
        }
        await self.publisher.publish(
            self.dlq_topic,
            json.dumps(payload).encode("utf-8"),
            {"job_id": event.job_id, "trace_id": event.trace_id},
        )
        if self.metrics:
            self.metrics.increment("dlq_messages_total", stage="ocr")

    async def _iterate_pages(self, event: DocumentIngestionEvent) -> AsyncIterator[str]:
        request = documentai.ProcessRequest(
            name=self.processor_name,
            skip_human_review=True,
            gcs_document=documentai.GcsDocument(
                gcs_uri=event.gcs_uri or f"gs://{event.bucket}/{event.object_name}",
                mime_type="application/pdf",
            ),
        )
        response = await self._process_with_retry(request)
        document = response.document
        for page in document.pages:
            yield self._extract_page_text(document, page)

    async def _process_with_retry(self, request: documentai.ProcessRequest) -> documentai.ProcessResponse:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(5),
            wait=wait_random_exponential(multiplier=1, max=30),
            reraise=True,
        ):
            with attempt:
                return await self._docai_client.process_document(request=request)
        raise RuntimeError("Document AI processing exhausted retries")

    @staticmethod
    def _extract_page_text(
        document: documentai.Document,
        page: documentai.Document.Page,
    ) -> str:
        text = document.text or ""
        segments: list[str] = []
        if page.paragraphs:
            for paragraph in page.paragraphs:
                segments.append(_layout_to_text(paragraph.layout, text))
        elif page.layout:
            segments.append(_layout_to_text(page.layout, text))
        return "\n".join(segment.strip() for segment in segments if segment.strip()).strip()


def _layout_to_text(layout: documentai.Document.Page.Layout, text: str) -> str:
    if not layout.text_anchor or not layout.text_anchor.text_segments:
        return ""
    pieces: list[str] = []
    for segment in layout.text_anchor.text_segments:
        start = int(segment.start_index or 0)
        end = int(segment.end_index or 0)
        pieces.append(text[start:end])
    return "".join(pieces)


__all__ = ["OCRService"]
