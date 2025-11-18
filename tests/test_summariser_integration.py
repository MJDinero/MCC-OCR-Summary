import pytest

from src.services.summariser_refactored import RefactoredSummariser, ChunkSummaryBackend

pytestmark = pytest.mark.integration


class DummyBackend(ChunkSummaryBackend):
    def __init__(self):
        self.calls = 0

    def summarise_chunk(
        self,
        *,
        chunk_text: str,
        chunk_index: int,
        total_chunks: int,
        estimated_tokens: int,
    ):
        self.calls += 1
        return {
            "provider_seen": [f"Team chunk {self.calls}"],
            "reason_for_visit": [f"Visit summary chunk {chunk_index + 1} of {total_chunks}"],
            "clinical_findings": [f"Detail {self.calls}"],
            "treatment_plan": [f"Plan {self.calls}"],
            "diagnoses": [f"D{self.calls}"],
            "healthcare_providers": [f"Dr Chunk {self.calls}"],
            "medications": [f"Med {self.calls}"],
        }


def test_large_input_multichunk_merge():
    backend = DummyBackend()
    summariser = RefactoredSummariser(backend=backend)
    summariser.chunk_target_chars = 100
    summariser.chunk_hard_max = 120
    big_text = " ".join(f"word{i}" for i in range(800))
    out = summariser.summarise(big_text)
    assert out["Medical Summary"]
    assert "_diagnoses_list" in out and "D1" in out["_diagnoses_list"]
    assert backend.calls >= 2
