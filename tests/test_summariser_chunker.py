from __future__ import annotations

from src.services.summariser import SummarizerChunker
from src.services import summariser as summariser_module


def test_sanitize_text_truncates_and_strips_control_chars():
    raw = "Hello\x00World\x07" + "A" * 5000
    cleaned = summariser_module._sanitize_text(raw, max_chars=10)
    assert "\x00" not in cleaned and "\x07" not in cleaned
    # Hard cap should limit to max_chars * 6 characters
    assert len(cleaned) == 60


def test_chunker_enforces_target_and_hard_max():
    chunker = SummarizerChunker(target_size=10, hard_max=15)
    text = "word " * 10 + "supercalifragilisticexpialidocious"
    chunks = chunker.split(text)
    assert len(chunks) >= 2
    assert all(len(chunk) <= 10 for chunk in chunks[:-1])
    assert chunks[-1] == "supercalifragilisticexpialidocious"


def test_default_chunker_respects_max(monkeypatch):
    text = " ".join(f"word{i}" for i in range(50))
    chunks = summariser_module._default_chunker(text, max_chars=40)
    assert all(len(chunk) <= 40 for chunk in chunks)
    assert len(chunks) > 1
