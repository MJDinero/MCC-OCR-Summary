from __future__ import annotations

import re
from typing import Dict


def test_summary_headers_and_forbidden_phrases(noisy_summary: Dict[str, str]) -> None:
    medical_summary = noisy_summary["Medical Summary"]
    expected_headers = (
        "Intro Overview:",
        "Key Points:",
        "Detailed Findings:",
        "Care Plan & Follow-Up:",
    )
    for header in expected_headers:
        assert header in medical_summary

    forbidden_phrases = (
        "Document processed in",
        "FEMALE PATIENTS PREGNANCY",
        "PLEASE FILL YOUR PRESCRIPTIONS",
        "Write the percentage relief",
        "Greater Plains Orthopedic",
        "I understand that",
    )
    low_summary = medical_summary.lower()
    for phrase in forbidden_phrases:
        assert phrase.lower() not in low_summary


def test_sections_drop_administrative_noise(noisy_summary: Dict[str, str]) -> None:
    medical_summary = noisy_summary["Medical Summary"]
    # Pull the detail section to ensure radiology impression retained while admin lines removed.
    detail_match = re.search(
        r"Detailed Findings:\n(?P<body>.+?)(\n\n[A-Z][^\n]+:|\Z)",
        medical_summary,
        flags=re.DOTALL,
    )
    assert detail_match is not None
    detail_body = detail_match.group("body").strip()
    assert detail_body, "Detailed Findings section should not be empty"
    lowered = detail_body.lower()
    assert "patient education" not in lowered
    assert "call the office immediately" not in lowered
    assert "document processed in" not in lowered
