"""Shared helpers for dynamic summary threshold calculations."""

from __future__ import annotations

import os

_DEFAULT_BASE_FLOOR = 120
_DEFAULT_RATIO = 0.35


def _safe_int(value: str | None, *, fallback: int) -> int:
    if value is None:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _safe_float(value: str | None, *, fallback: float) -> float:
    if value is None:
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def compute_summary_min_chars(ocr_chars: int, *, stub_mode: bool = False) -> int:
    """Return the minimum acceptable summary length for a given OCR payload."""
    if stub_mode:
        return 0

    env_floor_raw = os.getenv("MIN_SUMMARY_CHARS")
    base_floor = _DEFAULT_BASE_FLOOR
    if env_floor_raw is not None:
        parsed = max(0, _safe_int(env_floor_raw, fallback=_DEFAULT_BASE_FLOOR))
        base_floor = min(parsed, _DEFAULT_BASE_FLOOR)

    ratio = _safe_float(os.getenv("MIN_SUMMARY_DYNAMIC_RATIO"), fallback=_DEFAULT_RATIO)
    ratio = max(0.0, ratio)
    dynamic_floor = int(max(0, ocr_chars) * ratio)
    return max(base_floor, dynamic_floor)


__all__ = ["compute_summary_min_chars"]
