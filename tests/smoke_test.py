"""Lightweight smoke test invoking summarise_text wrapper.

This is distinct from scripts/smoke_test.py (which may perform offline manual runs).
The goal here is to ensure that the StructuredSummariser wrapper path remains functional
and returns the expected legacy-compatible keys plus side-channel list keys.
"""
from src.services.summariser import StructuredSummariser, OpenAIBackend


def test_smoke_summarise_text_wrapper():
    backend = OpenAIBackend(api_key="mock-local", model="mock-model")
    summariser = StructuredSummariser(backend=backend)
    sample_text = "Patient John Doe visited clinic for a routine checkup."
    result = summariser.summarise_text(sample_text)
    # Legacy keys
    assert "Medical Summary" in result
    # Side-channel keys
    assert "_diagnoses_list" in result
    assert "_providers_list" in result
    assert "_medications_list" in result
    assert isinstance(result["Medical Summary"], str)
