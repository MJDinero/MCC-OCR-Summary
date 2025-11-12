from __future__ import annotations

from typing import Any, Dict

import pytest
from fastapi import HTTPException

from src.api import process


def _section_map(sections: list[tuple[str, str]]) -> Dict[str, str]:
    return {heading: body for heading, body in sections}


def test_assemble_sections_uses_canonical_values() -> None:
    summarised: Dict[str, Any] = {
        "intro_overview": ["Facility context for this encounter."],
        "key_points": ["Primary complaint addressed.", "Follow-up arranged."],
        "detailed_findings": ["Vitals stable and imaging reviewed."],
        "care_plan": ["Continue current medication regimen."],
        "_diagnoses_list": "Dx1\nDx2",
        "_providers_list": ["Dr. Example"],
        "_medications_list": "MedA 10 mg daily",
    }

    sections = process._assemble_sections(summarised)
    sections_dict = _section_map(sections)

    assert sections_dict["Intro Overview"] == "Facility context for this encounter."
    assert sections_dict["Key Points"].startswith("- Primary complaint addressed.")
    assert sections_dict["Detailed Findings"].startswith("- Vitals stable")
    assert sections_dict["Care Plan & Follow-Up"].startswith(
        "- Continue current medication regimen."
    )
    assert sections_dict["Diagnoses"].startswith("- Dx1")
    assert sections_dict["Providers"].startswith("- Dr. Example")
    assert sections_dict["Medications / Prescriptions"].startswith("- MedA 10 mg daily")
    assert "Structured Indices" not in sections_dict
    assert "Structured content." not in sections_dict["Intro Overview"]


def test_assemble_sections_falls_back_when_empty() -> None:
    sections = process._assemble_sections({})
    expected = {
        "Intro Overview": process.SECTION_FALLBACK,
        "Key Points": process.SECTION_FALLBACK,
        "Detailed Findings": process.SECTION_FALLBACK,
        "Care Plan & Follow-Up": process.SECTION_FALLBACK,
        "Diagnoses": process.LIST_FALLBACK,
        "Providers": process.LIST_FALLBACK,
        "Medications / Prescriptions": process.LIST_FALLBACK,
    }
    section_map = _section_map(sections)
    for heading, text in expected.items():
        assert section_map[heading] == text


def test_pdf_validator_detects_forbidden_phrases() -> None:
    sections = [
        ("Intro Overview", "Document processed in 2 chunk(s). Overview text."),
        ("Key Points", "- Stable vitals."),
    ]
    compliant, hits = process._validate_pdf_sections(
        sections, guard_enabled=False
    )
    assert not compliant
    assert "document processed in" in hits


def test_pdf_validator_guard_raises() -> None:
    sections = [
        ("Intro Overview", "Structured Indices appear here."),
    ]
    with pytest.raises(HTTPException):
        process._validate_pdf_sections(sections, guard_enabled=True)
