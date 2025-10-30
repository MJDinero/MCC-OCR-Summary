"""Summarisation service: performs hierarchical summarisation on OCR chunks."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import AsyncIterator, Protocol

from tenacity import AsyncRetrying, stop_after_attempt, wait_random_exponential

from ..models.events import (
    OCRChunkMessage,
    StorageRequestMessage,
    SummaryRequestMessage,
    SummaryResultMessage,
)
from ..utils.redact import redact_mapping
from .chunker import Chunker
from .interfaces import MetricsClient, PubSubPublisher
from .metrics import PrometheusMetrics

LOG = logging.getLogger(__name__)


class LanguageModelClient(Protocol):
    """Minimal interface for LLM providers used in summarisation."""

    async def summarize(
        self,
        *,
        prompt: str,
        text: str,
        temperature: float,
        max_output_tokens: int,
        model: str,
    ) -> str: ...


class ChunkSummaryStore(Protocol):
    """Persistence layer for chunk-level summaries (for aggregation & retries)."""

    async def write_chunk_summary(
        self,
        *,
        record: SummaryResultMessage,
    ) -> None: ...

    async def list_chunk_summaries(
        self, *, job_id: str
    ) -> list[SummaryResultMessage]: ...


@dataclass(slots=True)
class SummarisationConfig:
    model_name: str
    temperature: float
    max_output_tokens: int
    chunk_size: int
    max_words: int


def build_prompt(doc_type: str, max_words: int) -> str:
    return (
        f"Summarize the following {doc_type} in under {max_words} words. "
        f"Use clear, factual, concise language."
    )


class SummarizationService:
    """Consumes OCR chunks, produces hierarchical summaries, and publishes to storage."""

    def __init__(
        self,
        *,
        publisher: PubSubPublisher,
        storage_topic: str,
        dlq_topic: str,
        llm_client: LanguageModelClient,
        store: ChunkSummaryStore,
        config: SummarisationConfig,
        metrics: MetricsClient | None = None,
    ) -> None:
        self.publisher = publisher
        self.storage_topic = storage_topic
        self.dlq_topic = dlq_topic
        self.llm_client = llm_client
        self.store = store
        self.config = config
        self.metrics = metrics or PrometheusMetrics.default()
        # Chunk OCR output into manageable sections for hierarchical summarisation
        self.section_chunker = Chunker(
            max_tokens=max(1024, config.chunk_size // 2),
            min_tokens=max(512, config.chunk_size // 4),
        )

    async def handle_chunk(self, message: OCRChunkMessage) -> None:
        """Process an OCR chunk and publish the resulting summary."""
        started = time.perf_counter()
        try:
            doc_type = (
                message.metadata.get("doc_type", "document")
                if message.metadata
                else "document"
            )
            partial_summaries = await self._summarize_sections(message, doc_type)
            chunk_summary = await self._summarize_text(
                "\n".join(partial_summaries),
                doc_type=doc_type,
                max_words=self.config.max_words,
            )
            summary_message = SummaryResultMessage(
                job_id=message.job_id,
                chunk_id=message.chunk_id,
                trace_id=message.trace_id,
                summary_text=chunk_summary,
                section_index=(
                    int(message.metadata.get("chunk_index", "0"))
                    if message.metadata
                    else 0
                ),
                total_sections=(
                    int(message.metadata.get("total_chunks", "0"))
                    if message.metadata
                    else 0
                ),
                tokens_used=len(chunk_summary),
                aggregate=False,
                metadata=message.metadata or {},
            )
            summary_message.metadata["partial_count"] = str(len(partial_summaries))
            await self.store.write_chunk_summary(record=summary_message)
            if self._is_last_chunk(message):
                await self._publish_final_summary(message, doc_type)
            duration = time.perf_counter() - started
            if self.metrics:
                self.metrics.observe_latency(
                    "summarization_latency_seconds",
                    duration,
                    stage="summarization",
                )
                self.metrics.increment("chunks_processed_total", stage="summarization")
            LOG.info(
                "summarization_chunk_completed",
                extra={
                    "job_id": message.job_id,
                    "trace_id": message.trace_id,
                    "chunk_id": message.chunk_id,
                    "latency_ms": int(duration * 1000),
                    "stage": "summarization",
                    "service": "summarization_service",
                    "redaction_applied": False,
                },
            )
        except Exception as exc:  # noqa: BLE001
            await self._handle_failure(message, exc)
            raise

    async def handle_aggregate(self, message: SummaryRequestMessage) -> None:
        """Aggregate chunk summaries into the final document summary."""
        await self._publish_final_summary(
            OCRChunkMessage(
                job_id=message.job_id,
                chunk_id=message.chunk_id,
                trace_id=message.trace_id,
                page_range=(0, 0),
                text="",
                metadata=message.metadata,
            ),
            doc_type=message.doc_type or "document",
        )

    async def _summarize_sections(
        self, message: OCRChunkMessage, doc_type: str
    ) -> list[str]:
        async def single_page() -> AsyncIterator[str]:
            yield message.text

        sections: list[str] = []
        async for chunk in self.section_chunker.chunk_async(single_page()):
            sections.append(chunk.text)
        if not sections:
            sections = [message.text]
        summaries: list[str] = []
        for section in sections:
            summary = await self._summarize_text(
                section,
                doc_type=doc_type,
                max_words=min(
                    self.config.max_words, max(50, self.config.max_words // 2)
                ),
            )
            summaries.append(summary)
        return summaries

    async def _summarize_text(self, text: str, *, doc_type: str, max_words: int) -> str:
        prompt = build_prompt(doc_type, max_words)
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_random_exponential(multiplier=1, max=20),
            reraise=True,
        ):
            with attempt:
                return await self.llm_client.summarize(
                    prompt=prompt,
                    text=text,
                    temperature=self.config.temperature,
                    max_output_tokens=self.config.max_output_tokens,
                    model=self.config.model_name,
                )
        raise RuntimeError("Summarisation retries exhausted")

    async def _publish_final_summary(
        self, message: OCRChunkMessage, doc_type: str
    ) -> None:
        started = time.perf_counter()
        summaries = await self.store.list_chunk_summaries(job_id=message.job_id)
        if not summaries:
            LOG.warning(
                "summarization_no_chunks",
                extra={
                    "job_id": message.job_id,
                    "trace_id": message.trace_id,
                    "stage": "summarization",
                    "service": "summarization_service",
                    "redaction_applied": False,
                },
            )
            return
        summaries.sort(key=lambda item: item.section_index)
        aggregate_text = "\n".join(summary.summary_text for summary in summaries)
        final_summary = await self._summarize_text(
            aggregate_text,
            doc_type=doc_type,
            max_words=self.config.max_words,
        )
        storage_message = StorageRequestMessage(
            job_id=message.job_id,
            trace_id=message.trace_id,
            final_summary=final_summary,
            per_chunk_summaries=summaries,
            object_uri=(
                message.metadata.get("source_uri", "") if message.metadata else ""
            ),
            metadata=message.metadata or {},
        )
        data, attributes = storage_message.to_pubsub()
        await self.publisher.publish(self.storage_topic, data, attributes)
        elapsed = time.perf_counter() - started
        if self.metrics:
            self.metrics.observe_latency(
                "summarization_aggregate_latency_seconds",
                elapsed,
                stage="summarization",
            )
            self.metrics.increment("jobs_completed_total", stage="summarization")
        LOG.info(
            "summarization_final_published",
            extra={
                "job_id": message.job_id,
                "trace_id": message.trace_id,
                "latency_ms": int(elapsed * 1000),
                "stage": "summarization",
                "service": "summarization_service",
                "redaction_applied": False,
            },
        )

    def _is_last_chunk(self, message: OCRChunkMessage) -> bool:
        if not message.metadata:
            return False
        return message.metadata.get("is_last_chunk", "false").lower() == "true"

    async def _handle_failure(self, message: OCRChunkMessage, error: Exception) -> None:
        LOG.exception(
            "summarization_failed",
            extra={
                "job_id": message.job_id,
                "trace_id": message.trace_id,
                "stage": "summarization",
                "service": "summarization_service",
                "error_type": type(error).__name__,
                "redaction_applied": True,
            },
        )
        payload = {
            "message": redact_mapping(message.metadata or {}),
            "error": {
                "type": type(error).__name__,
                "message": str(error),
            },
            "redaction_applied": True,
        }
        await self.publisher.publish(
            self.dlq_topic,
            json.dumps(payload).encode("utf-8"),
            {
                "error": "summarization_failed",
                "job_id": message.job_id,
                "trace_id": message.trace_id,
            },
        )
        if self.metrics:
            self.metrics.increment("dlq_messages_total", stage="summarization")


__all__ = [
    "SummarizationService",
    "SummarisationConfig",
    "LanguageModelClient",
    "ChunkSummaryStore",
    "build_prompt",
]
