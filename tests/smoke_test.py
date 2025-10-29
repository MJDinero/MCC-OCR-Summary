"""Lightweight smoke test for the refactored summariser wrapper."""

from src.services.summariser_refactored import RefactoredSummariser, ChunkSummaryBackend


class DummyBackend(ChunkSummaryBackend):
    def summarise_chunk(self, *, chunk_text, chunk_index, total_chunks, estimated_tokens):
        return {
            "overview": "Follow-up visit for chronic lumbar pain management.",
            "key_points": [
                "Patient reports persistent lumbar pain affecting daily activity tolerances."
            ],
            "clinical_details": [
                "MRI from March 2024 reviewed with no progression of degenerative changes."
            ],
            "care_plan": [
                "Continue physical therapy twice weekly and monitor symptom diary entries."
            ],
            "diagnoses": ["Lumbar strain with radicular symptoms noted on examination."],
            "providers": ["Dr. Test Provider assessed the patient."],
            "medications": ["Lisinopril 10 mg daily maintained without adjustment."],
        }


def test_smoke_refactored_summariser_wrapper():
    summariser = RefactoredSummariser(backend=DummyBackend())
    sample_text = "Patient John Doe visited clinic for a routine checkup." * 20
    result = summariser.summarise(sample_text)
    assert "Medical Summary" in result
    assert len(result["Medical Summary"]) >= summariser.min_summary_chars
    assert result["_diagnoses_list"].strip()
    assert result["_providers_list"].strip()
    assert result["_medications_list"].strip()
