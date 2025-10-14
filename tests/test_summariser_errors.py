import pytest

from src.services.summariser import Summariser
from src.errors import SummarizationError


class BackendMissingKeys:
    def summarise(self, text: str):  # returns only subset
        return { 'patient_info': 'Only patient info provided' }


class BackendRaises:
    def summarise(self, text: str):
        raise ValueError('boom')


def test_summariser_missing_keys_defaulted():
    s = Summariser(BackendMissingKeys())
    out = s.summarise('some text to summarise')
    assert out['Patient Information'].startswith('Only')
    # Missing keys should become 'N/A'
    assert out['Medical Summary'] == 'N/A'
    assert out['Billing Highlights'] == 'N/A'
    assert out['Legal / Notes'] == 'N/A'


def test_summariser_unexpected_error_wrapped():
    s = Summariser(BackendRaises())
    with pytest.raises(SummarizationError) as exc:
        s.summarise('anything')
    assert 'Unexpected summarisation error' in str(exc.value)
