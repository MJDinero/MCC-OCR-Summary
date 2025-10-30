from __future__ import annotations

import re
from typing import Dict


_DIAGNOSIS_ALLOWED = re.compile(r"^[A-Za-z0-9 ,.'/()%+-]+$")
_PROVIDER_ROLE = re.compile(
    r"\b(dr\.?|md|do|pa|np|rn|provider|physician)\b", re.IGNORECASE
)
_MEDICATION_MARKERS = re.compile(
    r"\b(mg|mcg|ml|units?|tablet|tab|capsule|cap|daily|bid|tid|qid|qhs|prn|inhaler|patch|cream|solution)\b",
    re.IGNORECASE,
)
_SENTENCE_PATTERN = re.compile(r"\.\s+[A-Z]")


def _non_empty_lines(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip()]


def test_diagnosis_entities_are_clean(noisy_summary: Dict[str, str]) -> None:
    diagnoses = _non_empty_lines(noisy_summary["_diagnoses_list"])
    assert diagnoses, "diagnosis list should not be empty"
    for item in diagnoses:
        low = item.lower()
        assert _DIAGNOSIS_ALLOWED.match(item), item
        assert "document processed in" not in low
        assert _SENTENCE_PATTERN.search(item) is None


def test_provider_entities_are_person_tokens(noisy_summary: Dict[str, str]) -> None:
    providers = _non_empty_lines(noisy_summary["_providers_list"])
    assert providers
    for item in providers:
        low = item.lower()
        assert _PROVIDER_ROLE.search(item), item
        assert "department" not in low


def test_medication_entities_include_dose_or_frequency(
    noisy_summary: Dict[str, str],
) -> None:
    medications = _non_empty_lines(noisy_summary["_medications_list"])
    assert medications
    for item in medications:
        low = item.lower()
        assert _MEDICATION_MARKERS.search(item), item
        assert "pharmacy only" not in low
        assert _SENTENCE_PATTERN.search(item) is None
