from __future__ import annotations

import pytest

from src.utils.summary_thresholds import compute_summary_min_chars


def test_compute_summary_min_chars_stub_mode() -> None:
    assert compute_summary_min_chars(ocr_chars=1000, stub_mode=True) == 0


@pytest.mark.parametrize(
    ("env_floor", "ratio", "ocr_chars", "expected"),
    [
        ("500", "0.01", 20000, 500),
        ("100", "0.05", 1000, 300),  # base floor clamps to default 300
    ],
)
def test_compute_summary_min_chars_env_overrides(
    monkeypatch, env_floor: str, ratio: str, ocr_chars: int, expected: int
) -> None:
    monkeypatch.setenv("MIN_SUMMARY_CHARS", env_floor)
    monkeypatch.setenv("MIN_SUMMARY_DYNAMIC_RATIO", ratio)
    result = compute_summary_min_chars(ocr_chars, stub_mode=False)
    assert result == expected


def test_compute_summary_min_chars_handles_invalid_env(monkeypatch) -> None:
    monkeypatch.setenv("MIN_SUMMARY_CHARS", "not-a-number")
    monkeypatch.setenv("MIN_SUMMARY_DYNAMIC_RATIO", "-1")
    result = compute_summary_min_chars(100, stub_mode=False)
    assert result == 300
