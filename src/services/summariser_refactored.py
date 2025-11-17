"""Compatibility shim for the refactored summariser stack.

Historically the refactored summariser lived entirely inside this module. The
new implementation has since moved into :mod:`src.services.summarization`
where the controller, formatter, backend implementations, and CLI tooling are
maintained.  This file keeps the legacy import path stable by re-exporting the
public API and thin wrappers around the command line helpers that the tests and
supervisor tooling expect.
"""

from __future__ import annotations

from typing import Iterable, Optional

from src.services.supervisor import CommonSenseSupervisor
from src.services.summarization import (
    CANONICAL_PDF_STRUCTURE,
    ChunkSummaryBackend,
    HeuristicChunkBackend,
    OpenAIResponsesBackend,
    RefactoredSummariser,
    build_pdf_sections_from_payload,
)
from src.services.summarization.cli import (
    _load_input_payload,
    _load_input_payload_from_gcs,
    _merge_dicts,
    _split_gcs_uri,
    _upload_summary_to_gcs,
    _write_output,
    run_cli,
)

__all__ = [
    "RefactoredSummariser",
    "ChunkSummaryBackend",
    "HeuristicChunkBackend",
    "OpenAIResponsesBackend",
    "CANONICAL_PDF_STRUCTURE",
    "build_pdf_sections_from_payload",
    "CommonSenseSupervisor",
    "_load_input_payload_from_gcs",
    "_load_input_payload",
    "_split_gcs_uri",
    "_merge_dicts",
    "_upload_summary_to_gcs",
    "_write_output",
    "_cli",
    "main",
]


def _cli(argv: Optional[Iterable[str]] = None) -> None:
    """Invoke the CLI entrypoint.

    The historical tests import :func:`_cli` directly, so we delegate to the new
    ``run_cli`` helper to keep behaviour identical while avoiding duplicated
    parsing logic across modules.
    """

    run_cli(argv)


def main(argv: Optional[Iterable[str]] = None) -> None:  # pragma: no cover - parity shim
    """Console entrypoint that mirrors ``python -m src.services.summariser_refactored``."""

    _cli(argv)


if __name__ == "__main__":  # pragma: no cover - module executed as script
    main()
