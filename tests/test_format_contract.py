from __future__ import annotations

import re
from typing import Dict


def test_summary_headers_and_forbidden_phrases(noisy_summary: Dict[str, str]) -> None:
    medical_summary = noisy_summary["Medical Summary"]
    expected_headers = (
        "Provider Seen:",
        "Reason for Visit:",
        "Clinical Findings:",
        "Treatment / Follow-up Plan:",
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
        "Structured Indices",
        "Summary Notes",
    )
    low_summary = medical_summary.lower()
    for phrase in forbidden_phrases:
        assert phrase.lower() not in low_summary


def test_sections_drop_administrative_noise(noisy_summary: Dict[str, str]) -> None:
    medical_summary = noisy_summary["Medical Summary"]
    # Pull the detail section to ensure radiology impression retained while admin lines removed.
    detail_match = re.search(
        r"Clinical Findings:\n(?P<body>.+?)(\n\n[A-Z][^\n]+:|\Z)",
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


def test_canonical_sections_expose_filtered_lists(noisy_summary: Dict[str, str]) -> None:
    provider_seen = noisy_summary["provider_seen"]
    assert isinstance(provider_seen, list)
    assert provider_seen
    for line in provider_seen:
        assert "document processed" not in line.lower()
    reason_lines = noisy_summary["reason_for_visit"]
    assert isinstance(reason_lines, list)
    assert reason_lines
    for point in reason_lines:
        assert "please fill your prescriptions" not in point.lower()
