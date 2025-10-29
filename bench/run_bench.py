#!/usr/bin/env python3
"""Benchmark harness for MCC OCR summarisation pipeline.

The script measures chunking throughput and summariser latency using the heuristics
backend so it can run locally without external APIs. Example:

    python bench/run_bench.py --input sample.txt --runs 5 --target-chars 6500
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from typing import Any, Dict, List

from src.services.docai_helper import clean_ocr_output
from src.services.summariser_refactored import RefactoredSummariser, HeuristicChunkBackend, SlidingWindowChunker

_DEFAULT_TEXT = (
    "Patient presents with chronic lumbar pain and limited range of motion. "
    "MRI from March 2024 shows multi-level degenerative disc disease without cord compromise. "
    "Provider recommends physical therapy, NSAIDs, and follow-up in eight weeks. "
    "Patient denies red-flag symptoms (saddle anesthesia, incontinence)."
) * 80


def _load_text(path: str | None) -> str:
    if not path:
        return _DEFAULT_TEXT
    candidate = Path(path)
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Input file not found: {candidate}")


def _run_once(text: str, target_chars: int, max_chars: int, overlap_chars: int) -> Dict[str, Any]:
    cleaned = clean_ocr_output(text)
    chunker = SlidingWindowChunker(
        target_chars=target_chars,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
    )
    chunks = chunker.split(cleaned)
    backend = HeuristicChunkBackend()
    summariser = RefactoredSummariser(
        backend=backend,
        target_chars=target_chars,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
    )
    start = time.perf_counter()
    summary = summariser.summarise(cleaned)
    duration = time.perf_counter() - start
    med_summary_chars = len(summary.get("Medical Summary", ""))
    chunk_lengths = [len(chunk.text) for chunk in chunks]
    return {
        "chunk_count": len(chunks),
        "avg_chunk_chars": statistics.mean(chunk_lengths) if chunk_lengths else 0,
        "p95_chunk_chars": statistics.quantiles(chunk_lengths, n=20)[-1] if len(chunk_lengths) >= 20 else max(chunk_lengths or [0]),
        "summary_chars": med_summary_chars,
        "latency_ms": round(duration * 1000, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark MCC chunking + summarisation flow")
    parser.add_argument("--input", help="Path to text payload (defaults to built-in synthetic note)")
    parser.add_argument("--runs", type=int, default=5, help="Number of benchmark iterations")
    parser.add_argument("--target-chars", type=int, default=6500)
    parser.add_argument("--max-chars", type=int, default=8500)
    parser.add_argument("--overlap-chars", type=int, default=900)
    args = parser.parse_args()

    text = _load_text(args.input)
    results: List[Dict[str, Any]] = []
    for _ in range(max(1, args.runs)):
        results.append(
            _run_once(
                text,
                target_chars=args.target_chars,
                max_chars=args.max_chars,
                overlap_chars=args.overlap_chars,
            )
        )
    latency = [item["latency_ms"] for item in results]
    summary_lengths = [item["summary_chars"] for item in results]
    aggregate = {
        "runs": len(results),
        "target_chars": args.target_chars,
        "max_chars": args.max_chars,
        "overlap_chars": args.overlap_chars,
        "latency_ms_avg": round(statistics.mean(latency), 2),
        "latency_ms_p95": round(statistics.quantiles(latency, n=20)[-1] if len(latency) >= 20 else max(latency), 2),
        "summary_chars_avg": round(statistics.mean(summary_lengths), 1),
        "chunk_count_avg": round(statistics.mean(item["chunk_count"] for item in results), 2),
        "avg_chunk_chars": round(statistics.mean(item["avg_chunk_chars"] for item in results), 2),
    }
    payload = {"aggregate": aggregate, "samples": results}
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
