#!/usr/bin/env python3
"""Benchmark MCC OCR Summary pipeline on synthetic large documents."""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path
import sys
from dataclasses import dataclass
from statistics import mean
from typing import Any, AsyncIterator, Iterable, Sequence

# Ensure the repository root is on sys.path for script execution.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.models.events import OCRChunkMessage
from src.services.chunker import Chunk, Chunker
from src.services.metrics import NullMetrics
from src.services.summarization_service import (
    SummarisationConfig,
    SummarizationService,
)
from src.services.summary_store import InMemoryChunkSummaryStore


@dataclass(slots=True)
class BenchmarkResult:
    pages: int
    duration: float
    chunk_count: int
    summary_length: int


class StubPublisher:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bytes, dict[str, str]]] = []

    async def publish(self, topic: str, data: bytes, attributes: dict[str, str] | None = None) -> str:
        self.messages.append((topic, data, attributes or {}))
        return "benchmark"


class EchoLLM:
    async def summarize(
        self,
        *,
        prompt: str,
        text: str,
        temperature: float,
        max_output_tokens: int,
        model: str,
    ) -> str:
        words = text.split()
        limit = min(len(words), max(10, max_output_tokens // 4))
        return " ".join(words[:limit])


async def run_benchmark(pages: int, words_per_page: int) -> BenchmarkResult:
    publisher = StubPublisher()
    store = InMemoryChunkSummaryStore()
    summarizer = SummarizationService(
        publisher=publisher,
        storage_topic="benchmark-storage",
        dlq_topic="benchmark-dlq",
        llm_client=EchoLLM(),
        store=store,
        config=SummarisationConfig(
            model_name="echo",
            temperature=0.0,
            max_output_tokens=512,
            chunk_size=4000,
            max_words=200,
        ),
        metrics=NullMetrics(),
    )

    text_page = " ".join(f"word{i}" for i in range(words_per_page))
    ocr_chunker = Chunker()
    pages_text = [text_page for _ in range(pages)]

    async def _page_source() -> AsyncIterator[str]:
        for page in pages_text:
            yield page

    chunk_messages: list[OCRChunkMessage] = []
    chunk_index = 0
    chunk_list: list[Chunk] = []
    async for chunk in ocr_chunker.chunk_async(_page_source()):  # type: ignore[arg-type]
        chunk_list.append(chunk)

    total_chunks = len(chunk_list)
    for chunk in chunk_list:
        metadata = {
            "chunk_index": str(chunk_index),
            "is_last_chunk": str(chunk_index == total_chunks - 1).lower(),
            "total_chunks": str(total_chunks),
            "source_uri": f"benchmark://document/{pages}",
        }
        chunk_messages.append(
            OCRChunkMessage(
                job_id=f"bench-{pages}",
                chunk_id=f"chunk-{chunk_index}",
                trace_id="benchmark-trace",
                page_range=(chunk.page_start, chunk.page_end),
                text=chunk.text,
                metadata=metadata,
            )
        )
        chunk_index += 1

    start = time.perf_counter()
    for message in chunk_messages:
        await summarizer.handle_chunk(message)
    duration = time.perf_counter() - start
    summary_length = sum(len(msg.summary_text) for msg in await store.list_chunk_summaries(job_id=f"bench-{pages}"))
    return BenchmarkResult(
        pages=pages,
        duration=duration,
        chunk_count=total_chunks or 0,
        summary_length=summary_length,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pages",
        type=int,
        nargs="+",
        default=[10, 50, 200, 500],
        help="List of page counts to benchmark.",
    )
    parser.add_argument(
        "--words-per-page",
        type=int,
        default=350,
        help="Synthetic words per page used for the benchmark.",
    )
    args = parser.parse_args()

    results = asyncio.run(_run_all(args.pages, args.words_per_page))
    print("\nBenchmark results (synthetic document stream)")
    print("Pages | Chunks | Duration (s)")
    for result in results:
        print(f"{result.pages:5d} | {result.chunk_count:6d} | {result.duration:12.2f}")

    avg = mean(result.duration for result in results)
    print(f"\nAverage duration: {avg:.2f}s")


async def _run_all(pages: Sequence[int], words_per_page: int) -> list[BenchmarkResult]:
    results: list[BenchmarkResult] = []
    for page_count in pages:
        results.append(await run_benchmark(page_count, words_per_page))
    return results


if __name__ == "__main__":
    main()
