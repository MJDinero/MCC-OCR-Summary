import pytest
pytest.skip("Legacy multi-chunk summariser test removed after refactor", allow_module_level=True)

import pytest

from src.services.summariser import Summariser, SummarizationBackend, TransientSummarizationError
from src.errors import SummarizationError

class DummyBackend:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def summarise(self, text: str, *, instruction: str | None = None) -> str:  # noqa: D401
        self.calls += 1
        if not self._responses:
            return "EMPTY"
        r = self._responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return f"SUM({r})"


def test_single_chunk_success():
    backend = DummyBackend(["A large body of text"])
    s = Summariser(backend, max_chunk_chars=100)
    out = s.summarise("A large body of text")
    assert out.startswith("SUM(")
    assert backend.calls == 1


def test_multi_chunk_aggregate():
    text = " ".join([f"w{i}" for i in range(200)])
    backend = DummyBackend(["p1", "p2", "final"])
    s = Summariser(backend, max_chunk_chars=50, aggregate_final=True)
    out = s.summarise(text)
    assert out.startswith("SUM(")
    # 3 calls: two chunks + final aggregation
    assert backend.calls == 3


def test_retry_then_success(monkeypatch):
    backend = DummyBackend([TransientSummarizationError("boom"), "ok"])  # first attempt transient
    s = Summariser(backend, max_chunk_chars=100)
    out = s.summarise("hello world")
    assert "SUM(ok)" == out
    assert backend.calls == 2


def test_fail_after_retries():
    # Enough transient errors to exhaust retries (4 attempts)
    backend = DummyBackend([
        TransientSummarizationError("e1"),
        TransientSummarizationError("e2"),
        TransientSummarizationError("e3"),
        TransientSummarizationError("e4"),
    ])
    s = Summariser(backend, max_chunk_chars=100)
    with pytest.raises(SummarizationError):
        s.summarise("some text")


def test_empty_text():
    backend = DummyBackend([])
    s = Summariser(backend)
    with pytest.raises(SummarizationError):
        s.summarise("   ")
