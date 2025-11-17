"""Summarisation package exports."""

from .backend import (
    ChunkSummaryBackend,
    HeuristicChunkBackend,
    OpenAIResponsesBackend,
    SlidingWindowChunker,
    ChunkedText,
)
from .controller import RefactoredSummariser
from .cli import run_cli
from .formatter import (
    CANONICAL_PDF_STRUCTURE,
    CanonicalFormatter,
    CanonicalSummary,
    build_pdf_sections_from_payload,
)

__all__ = [
    "ChunkSummaryBackend",
    "HeuristicChunkBackend",
    "OpenAIResponsesBackend",
    "SlidingWindowChunker",
    "ChunkedText",
    "RefactoredSummariser",
    "CanonicalFormatter",
    "CanonicalSummary",
    "CANONICAL_PDF_STRUCTURE",
    "build_pdf_sections_from_payload",
    "run_cli",
]
