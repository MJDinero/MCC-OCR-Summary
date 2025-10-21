import pytest

from src.services.summariser import StructuredSummariser

pytestmark = pytest.mark.integration


class DummyBackend:
    def __init__(self):
        # Return minimal structured fields with synthetic variation
        self.calls = 0
    def summarise(self, text: str):  # returns per-chunk structured JSON
        self.calls += 1
        return {
            'provider_seen': f'Dr Smith chunk {self.calls}',
            'reason_for_visit': 'Follow up',
            'clinical_findings': f'Findings {self.calls}',
            'treatment_plan': 'Plan stable',
            'diagnoses': ['D1', 'D2'],
            'providers': ['Dr Smith'],
            'medications': ['MedA']
        }


def test_large_input_multichunk_merge():
    backend = DummyBackend()
    s = StructuredSummariser(backend, chunk_target_chars=100, chunk_hard_max=120, multi_chunk_threshold=120)
    big_text = ("word " * 800)  # large enough to force many chunks at 100 char target
    out = s.summarise(big_text)
    assert out['Medical Summary']
    # Side channel lists preserved
    assert '_diagnoses_list' in out and 'D1' in out['_diagnoses_list']
    # At least 2 chunk calls
    assert backend.calls >= 2
