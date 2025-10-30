#!/usr/bin/env python3
"""Lightweight smoke test for the refactored summariser.

Generates a synthetic OCR payload, runs through RefactoredSummariser with a
stub backend (no OpenAI call) and asserts output structure.
"""
from __future__ import annotations

from src.services.summariser_refactored import RefactoredSummariser, ChunkSummaryBackend


class _DummyBackend(ChunkSummaryBackend):
    def __init__(self):
        self.calls = 0

    def summarise_chunk(
        self, *, chunk_text, chunk_index, total_chunks, estimated_tokens
    ):  # pragma: no cover - deterministic stub
        self.calls += 1
        return {
            "overview": f"Summary chunk {chunk_index + 1}",
            "key_points": [f"Key point {self.calls}"],
            "clinical_details": [f"Detail {self.calls}"],
            "care_plan": [f"Plan {self.calls}"],
            "diagnoses": ["DXA", "DXB"],
            "providers": ["Dr Example"],
            "medications": ["MedX"],
        }


def run():
    backend = _DummyBackend()
    summariser = RefactoredSummariser(backend=backend)
    summariser.chunk_target_chars = 500
    summariser.chunk_hard_max = 600
    text = "lorem ipsum dolor sit amet " * 1200
    result = summariser.summarise(text)
    assert "Medical Summary" in result
    assert result["_diagnoses_list"].startswith("DXA")
    assert backend.calls >= 2, "Expected multi-chunk operation"
    lines = result["Medical Summary"].splitlines()
    assert len(lines) > 5
    print("SMOKE TEST PASS:", "chunks=", backend.calls, "lines=", len(lines))


if __name__ == "__main__":  # pragma: no cover
    run()
