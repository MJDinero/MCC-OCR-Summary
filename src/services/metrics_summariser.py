"""Prometheus metrics for summarisation runtime quality."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

CHUNKS_TOTAL = Counter(
    "summariser_chunks_total",
    "Number of chunks processed per summary run",
)
FALLBACK_RUNS_TOTAL = Counter(
    "summariser_fallback_runs_total",
    "Number of heuristic fallback executions",
)
NEEDS_REVIEW_TOTAL = Counter(
    "summariser_needs_review_total",
    "Summaries flagged for human review",
)
COLLAPSE_RUNS_TOTAL = Counter(
    "summariser_collapse_runs_total",
    "Number of times summaries were collapsed to fit length thresholds",
)
CHUNK_CHARS_HISTOGRAM = Histogram(
    "summariser_chunk_chars",
    "Distribution of chunk character lengths",
    buckets=(500, 1000, 2000, 4000, 6500, 8500, 10000, float("inf")),
)

_STATE = {
    "chunks_total": 0.0,
    "chunk_chars_total": 0.0,
    "chunk_chars_count": 0.0,
    "fallback_runs": 0.0,
    "needs_review": 0.0,
    "collapse_runs": 0.0,
}


def record_chunks(count: int) -> None:
    if count <= 0:
        return
    CHUNKS_TOTAL.inc(count)
    _STATE["chunks_total"] += count


def record_chunk_chars(chars: int) -> None:
    if chars <= 0:
        return
    CHUNK_CHARS_HISTOGRAM.observe(chars)
    _STATE["chunk_chars_total"] += chars
    _STATE["chunk_chars_count"] += 1


def record_fallback_run() -> None:
    FALLBACK_RUNS_TOTAL.inc()
    _STATE["fallback_runs"] += 1


def record_needs_review() -> None:
    NEEDS_REVIEW_TOTAL.inc()
    _STATE["needs_review"] += 1


def record_collapse_run() -> None:
    COLLAPSE_RUNS_TOTAL.inc()
    _STATE["collapse_runs"] += 1


def snapshot_state() -> dict[str, float]:
    avg_chunk = (
        _STATE["chunk_chars_total"] / _STATE["chunk_chars_count"]
        if _STATE["chunk_chars_count"]
        else 0.0
    )
    return {
        "chunks_total": _STATE["chunks_total"],
        "avg_chunk_chars": avg_chunk,
        "fallback_runs": _STATE["fallback_runs"],
        "needs_review": _STATE["needs_review"],
        "collapse_runs": _STATE["collapse_runs"],
    }


def reset_state() -> None:
    for key in _STATE:
        _STATE[key] = 0.0


__all__ = [
    "record_chunks",
    "record_chunk_chars",
    "record_fallback_run",
    "record_needs_review",
    "record_collapse_run",
    "snapshot_state",
    "reset_state",
]
