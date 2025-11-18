from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

import pytest

from src.services.summariser_refactored import (
    ChunkSummaryBackend,
    RefactoredSummariser,
)


@dataclass
class _NoisyBackend(ChunkSummaryBackend):
    payload: Dict[str, Any]

    def summarise_chunk(
        self,
        *,
        chunk_text: str,
        chunk_index: int,
        total_chunks: int,
        estimated_tokens: int,
    ) -> Dict[str, Any]:
        return dict(self.payload)


@pytest.fixture(scope="module")
def noisy_summary() -> Dict[str, str]:
    backend = _NoisyBackend(
        payload={
            "provider_seen": [
                "Dr. Mark Lee, MD at Greater Plains Orthopedic billing department.",
                "Document processed in 2 chunk(s) per facility log.",
            ],
            "reason_for_visit": [
                "Patient evaluated after lumbar procedure. Document processed in 2 chunk(s).",
                "FEMALE PATIENTS PREGNANCY warning and privacy notice paragraphs were included.",
                "Patient ambulating with minimal discomfort. PLEASE FILL YOUR PRESCRIPTIONS at hospital pharmacy.",
            ],
            "clinical_findings": [
                "Impression: MRI lumbar spine shows post-surgical changes without infection.",
                "Call the office immediately if pain escalates; patient education materials attached.",
            ],
            "treatment_plan": [
                "Follow-up with primary care provider in two weeks for wound check.",
                "I understand that the following care instructions apply to Greater Plains Orthopedic.",
            ],
            "diagnoses": [
                "M54.5 Low back pain",
                "Document processed in 2 chunk(s)",
            ],
            "healthcare_providers": [
                "Dr. Mark Lee, MD",
                "Greater Plains Orthopedic billing department",
            ],
            "medications": [
                "Lisinopril 10 mg daily",
                "Pharmacy ONLY: call for refills",
            ],
            "schema_version": "2025-11-16",
        }
    )
    summariser = RefactoredSummariser(
        backend=backend, target_chars=320, max_chars=420, overlap_chars=40
    )
    noisy_text = (
        "Synthetic OCR text describing the operative report and discharge paperwork. "
        "Instructions repeatedly mention consent forms, patient education notices, "
        "and pharmacy-only refill guidance."
    ) * 8
    return summariser.summarise(
        noisy_text, doc_metadata={"facility": "Greater Plains Orthopedic"}
    )
