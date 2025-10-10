from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any

import pytest

from src.errors import SummarizationError
from src.services.summariser_refactored import RefactoredSummariser, ChunkSummaryBackend


@dataclass
class StubBackend(ChunkSummaryBackend):
    responses: Dict[int, Dict[str, Any]]

    def summarise_chunk(
        self,
        *,
        chunk_text: str,
        chunk_index: int,
        total_chunks: int,
        estimated_tokens: int,
    ) -> Dict[str, Any]:
        payload = self.responses.get(chunk_index)
        if not payload:
            fallback_index = max(self.responses)
            payload = self.responses[fallback_index]
        # Include a snippet of the chunk to ensure backend sees the same data across invocations.
        result = dict(payload)
        result.setdefault("overview", f"Chunk {chunk_index} covers {len(chunk_text.split())} words.")
        return result


def test_refactored_summary_structure_and_length() -> None:
    text = (
        "Patient Jane Doe visited the clinic complaining of persistent migraines. "
        "Neurological examination revealed no focal deficits. MRI performed in March 2024 was normal. "
        "Blood pressure measured at 138/88 mmHg. Provider Dr. Alicia Carter discussed lifestyle modifications. "
        "Sumatriptan 50 mg prescribed to be taken at onset of migraine. Follow-up arranged in 6 weeks. "
        "Patient also reports mild anxiety managed with cognitive behavioural therapy."
    ) * 5

    backend = StubBackend(
        responses={
            1: {
                "overview": "Follow-up neurology visit for chronic migraines without aura.",
                "key_points": [
                    "Patient reports increased migraine frequency impacting daily activities.",
                    "Normal neurological examination with stable vitals.",
                ],
                "clinical_details": [
                    "Blood pressure 138/88 mmHg; neurological exam non-focal.",
                    "MRI from March 2024 reviewed and remains normal.",
                ],
                "care_plan": [
                    "Continue sumatriptan 50 mg at migraine onset and track response.",
                    "Reinforce hydration, sleep hygiene, and headache diary usage.",
                ],
                "diagnoses": ["G43.709 Chronic migraine without aura"],
                "providers": ["Dr. Alicia Carter"],
                "medications": ["Sumatriptan 50 mg as needed"],
            },
            2: {
                "key_points": [
                    "Patient engaging in cognitive behavioural therapy for anxiety symptoms.",
                ],
                "clinical_details": [
                    "Patient denies visual changes, motor weakness, or speech disturbance.",
                    "Reports mild anxiety managed through behavioural interventions.",
                ],
                "care_plan": [
                    "Schedule follow-up neurology visit in 6 weeks to reassess frequency and treatment response.",
                ],
                "diagnoses": ["F41.9 Anxiety disorder, unspecified"],
                "providers": ["Clinic behavioural health team"],
                "medications": ["Cognitive behavioural therapy"],
            },
        }
    )

    summariser = RefactoredSummariser(backend=backend, target_chars=300, max_chars=420, overlap_chars=60)
    summary = summariser.summarise(text, doc_metadata={"facility": "MCC Neurology"})
    medical_summary = summary["Medical Summary"]

    assert "Intro Overview:" in medical_summary
    assert "Key Points:" in medical_summary
    assert "Detailed Findings:" in medical_summary
    assert "Care Plan & Follow-Up:" in medical_summary
    assert len(medical_summary) >= summariser.min_summary_chars

    diagnoses_lines = summary["_diagnoses_list"].splitlines()
    assert "G43.709 Chronic migraine without aura" in diagnoses_lines[0]
    assert summary["_providers_list"].strip().startswith("Dr. Alicia Carter")


def test_refactored_summary_requires_non_empty_text() -> None:
    backend = StubBackend(responses={1: {"overview": "Empty"}})
    summariser = RefactoredSummariser(backend=backend)
    with pytest.raises(SummarizationError):
        summariser.summarise("   ")


def test_compose_summary_pads_short_outputs() -> None:
    backend = StubBackend(
        responses={
            1: {
                "overview": "Brief visit for vaccination update.",
                "key_points": ["Tdap booster administered."],
                "clinical_details": ["No adverse reactions documented."],
                "care_plan": ["Monitor for injection site soreness."],
            }
        }
    )
    summariser = RefactoredSummariser(backend=backend, min_summary_chars=300)
    result = summariser.summarise("Tdap booster provided.")
    assert len(result["Medical Summary"]) >= 300
