"""Lightweight smoke test for the refactored summariser wrapper."""

from src.models.summary_contract import SummaryContract
from src.services.summariser_refactored import RefactoredSummariser, ChunkSummaryBackend


class DummyBackend(ChunkSummaryBackend):
    def summarise_chunk(
        self, *, chunk_text, chunk_index, total_chunks, estimated_tokens
    ):
        return {
            "overview": "Patient summary",
            "key_points": ["Key details"],
            "clinical_details": ["Clinical detail"],
            "care_plan": ["Care plan"],
            "diagnoses": ["Dx"],
            "providers": ["Dr Test"],
            "medications": ["Med"],
        }


def test_smoke_refactored_summariser_wrapper():
    summariser = RefactoredSummariser(backend=DummyBackend())
    sample_text = "Patient John Doe visited clinic for a routine checkup." * 20
    result = summariser.summarise(sample_text)
    contract = SummaryContract.from_mapping(result)
    assert contract.sections
    assert len(contract.as_text()) > 0
    slug_index = {section.slug: section for section in contract.sections}
    assert slug_index["diagnoses"].extra.get("items")
    assert slug_index["healthcare_providers"].extra.get("items")
    assert slug_index["medications"].extra.get("items")
