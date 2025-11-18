"""Lightweight smoke test for the refactored summariser wrapper."""

from src.services.summariser_refactored import RefactoredSummariser, ChunkSummaryBackend


class DummyBackend(ChunkSummaryBackend):
    def summarise_chunk(
        self, *, chunk_text, chunk_index, total_chunks, estimated_tokens
    ):
        return {
            "provider_seen": ["Patient summary"],
            "reason_for_visit": ["Key details"],
            "clinical_findings": ["Clinical detail"],
            "treatment_plan": ["Care plan"],
            "diagnoses": ["Dx"],
            "healthcare_providers": ["Dr Test"],
            "medications": ["Lisinopril 20 mg daily"],
        }


def test_smoke_refactored_summariser_wrapper():
    summariser = RefactoredSummariser(backend=DummyBackend())
    sample_text = "Patient John Doe visited clinic for a routine checkup." * 20
    result = summariser.summarise(sample_text)
    assert "Medical Summary" in result
    assert len(result["Medical Summary"]) > 0
    assert result["_diagnoses_list"].strip()
    assert result["_providers_list"].strip()
    assert result["_medications_list"].strip()
