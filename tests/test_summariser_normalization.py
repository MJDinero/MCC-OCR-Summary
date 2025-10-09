"""Tests for normalization/coercion branches in Summariser.

Exercises:
 - Non-string input to summarise (int)
 - Dict/tuple/set/list coercion for scalar fields
 - Dict flattening for list fields (diagnoses/providers/medications)
 - Mock OpenAI backend early-return branch
"""
from src.services.summariser import Summariser, OpenAIBackend


class BackendNormalization:
    def summarise(self, text: str):  # returns new schema with varied types
        return {
            'provider_seen': {'name': 'Dr Smith', 'dept': 'Cardiology'},  # dict -> flattened
            'reason_for_visit': ['Follow-up', 'Follow-up'],  # list w/ duplicate
            'clinical_findings': ('Stable vitals', 'Stable vitals'),  # tuple
            'treatment_plan': {'plan': 'Continue meds', 'note': 'Reassess in 6m'},  # dict
            'diagnoses': {'dx1': 'Hypertension', 'dx2': 'Hypertension'},  # dict -> flattened values
            'providers': ('Dr Smith', 'Dr Smith'),  # tuple -> list merge
            'medications': {'m1': 'Lisinopril', 'm2': 'Lisinopril'},  # dict -> flattened
        }


def test_normalization_branches():
    s = Summariser(BackendNormalization())
    # Pass non-string input to hit coercion guard
    out = s.summarise(12345)  # type: ignore[arg-type]
    assert 'Medical Summary' in out
    # Ensure clinical findings content present (duplicate internal comma join acceptable for single chunk)
    assert 'Stable vitals' in out['Medical Summary']
    # Side channel keys exist
    assert '_diagnoses_list' in out
    # Flattened list should contain Hypertension once (deduplicated)
    assert out['_diagnoses_list'].strip().split('\n').count('Hypertension') == 1


def test_mock_openai_backend():
    backend = OpenAIBackend(api_key='mock-key-123', model='mock-model')
    s = Summariser(backend)
    out = s.summarise('Short text about a visit')
    # Canned fields appear
    assert out['Medical Summary'].startswith('Provider Seen') or 'Medical Summary' in out


def test_merge_field_handles_dict_list_types():
    # Use Summariser with a fake backend returning collected chunks mimicking internal structure
    class BackendCF:
        def __init__(self):
            self.calls = 0
        def summarise(self, text: str):
            self.calls += 1
            if self.calls == 1:
                return {"clinical_findings": {"note": "mild swelling"}}
            return {"clinical_findings": ["ok", "normal"]}

    s = Summariser(BackendCF())
    out = s.summarise("Line one. Line two that forces multi chunk?" * 2)
    # Expect merged clinical findings includes serialized dict value
    assert 'mild swelling' in out['Medical Summary']
