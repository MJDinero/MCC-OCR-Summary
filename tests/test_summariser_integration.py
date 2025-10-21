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
            "overview": f"Visit summary chunk {chunk_index + 1} of {total_chunks}",
            "key_points": [f"Key point {self.calls}"],
            "clinical_details": [f"Detail {self.calls}"],
            "care_plan": [f"Plan {self.calls}"],
            "diagnoses": [f"D{self.calls}"],
            "providers": [f"Dr Chunk {self.calls}"],
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
