#!/usr/bin/env python3
"""Lightweight smoke test for the refactored summariser.

Generates a synthetic OCR payload, runs through RefactoredSummariser with a
stub backend (no OpenAI call) and asserts SummaryContract structure."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.models.summary_contract import SummaryContract
from src.services.summariser_refactored import ChunkSummaryBackend, RefactoredSummariser


def _load_validator_module():
    spec = importlib.util.spec_from_file_location(
        "scripts.validate_summary", ROOT / "scripts" / "validate_summary.py"
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive guard
        raise RuntimeError("Unable to load scripts/validate_summary.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_VALIDATOR = _load_validator_module()
_REQUIRED_HEADINGS: Sequence[str] = tuple(_VALIDATOR.DEFAULT_REQUIRED_HEADINGS)
_VALIDATE_CLAIMS = getattr(_VALIDATOR, "_validate_claims")


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


def _assert_required_sections(contract: SummaryContract) -> None:
    titles = {
        section.title.strip().lower()
        for section in contract.sections
        if section.title and section.kind == "mcc"
    }
    missing = [heading for heading in _REQUIRED_HEADINGS if heading.lower() not in titles]
    if missing:
        raise AssertionError(f"contract missing MCC headings: {missing}")


def run():
    backend = _DummyBackend()
    summariser = RefactoredSummariser(backend=backend)
    summariser.chunk_target_chars = 500
    summariser.chunk_hard_max = 600
    text = "lorem ipsum dolor sit amet " * 1200
    result = summariser.summarise(text)
    contract = SummaryContract.from_mapping(result)
    assert contract.schema_version, "Contract missing schema version"
    _assert_required_sections(contract)
    _VALIDATE_CLAIMS(contract.to_dict(), strict=False)
    assert backend.calls >= 2, "Expected multi-chunk operation"
    print(
        "SMOKE TEST PASS:",
        f"chunks={backend.calls}",
        f"sections={len(contract.sections)}",
        f"claims={len(contract.claims)}",
    )


if __name__ == "__main__":  # pragma: no cover
    run()
