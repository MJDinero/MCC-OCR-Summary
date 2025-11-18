from __future__ import annotations

from typing import Dict, List

from src.services.summarization.formatter import (
    CanonicalFormatter,
    CanonicalSummary,
    build_pdf_sections_from_payload,
)


def test_canonical_formatter_compose_returns_canonical_sections() -> None:
    aggregated: Dict[str, List[str]] = {
        "provider_seen": ["Patient seen at clinic for follow-up."],
        "reason_for_visit": [
            "Patient returns for chronic knee pain follow-up.",
            "Patient returns for chronic knee pain follow-up.",  # duplicate
            "Patient discussed imaging results.",
        ],
        "clinical_findings": [
            "BP 120/70, HR 70, exam otherwise normal.",
            "MRI from 10/12 reviewed with patient.",
        ],
        "treatment_plan": ["Continue PT and return in 6 weeks."],
        "diagnoses": ["Chronic knee pain"],
        "healthcare_providers": ["Dr. Example, MD"],
        "medications": ["Ibuprofen 400 mg BID"],
    }

    formatter = CanonicalFormatter()
    summary = formatter.compose(aggregated)

    assert summary.sections["Provider Seen"][0].startswith("Patient seen")
    reason_lines = summary.sections["Reason for Visit"]
    assert reason_lines[0].startswith("Patient returns")
    assert reason_lines  # at least one entry emitted
    assert reason_lines.count(reason_lines[0]) == 1  # duplicate removed
    assert summary.diagnoses == ["Chronic knee pain"]
    assert summary.providers == ["Dr. Example, MD"]
    assert summary.medications == ["Ibuprofen 400 mg BID"]


def test_build_pdf_sections_from_payload_prefers_canonical_fields() -> None:
    payload = {
        "Medical Summary": "Provider Seen:\n- Sample intro",
        "_canonical_sections": {
            "Provider Seen": ["Sample intro"],
            "Reason for Visit": ["Visit reason"],
            "Clinical Findings": ["Vitals documented"],
            "Treatment / Follow-up Plan": ["Return in 6 weeks"],
        },
        "_canonical_entities": {
            "Diagnoses": ["Chronic knee pain"],
            "Healthcare Providers": ["Dr. Example"],
            "Medications / Prescriptions": ["Ibuprofen"],
        },
    }

    sections = build_pdf_sections_from_payload(payload)
    section_map = {heading: body for heading, body in sections}
    assert section_map["Provider Seen"] == "Sample intro"
    assert section_map["Reason for Visit"].startswith("- Visit reason")
    assert "- Chronic knee pain" in section_map["Diagnoses"]


def test_build_pdf_sections_handles_legacy_payload() -> None:
    payload = {
        "overview": "Legacy intro",
        "key_points": ["First", "Second"],
        "clinical_details": "Vitals stable",
        "care_plan": ["Return PRN"],
        "_diagnoses_list": "Diag",
        "_providers_list": "Provider",
        "_medications_list": "Medication",
    }
    sections = build_pdf_sections_from_payload(payload)
    section_map = {heading: body for heading, body in sections}
    assert section_map["Provider Seen"] == "Legacy intro"
    assert section_map["Reason for Visit"].startswith("- First")
    assert section_map["Medications / Prescriptions"].startswith("- Medication")


def test_canonical_summary_helpers_roundtrip() -> None:
    summary = CanonicalSummary(
        summary_text="Provider Seen:\n- Sample visit summary",
        sections={
            "Provider Seen": ["Patient evaluated in clinic."],
            "Reason for Visit": ["Follow-up for diabetes management."],
            "Clinical Findings": ["A1C improved to 7.1%."],
            "Treatment / Follow-up Plan": ["Continue metformin and log glucose."],
        },
        diagnoses=["E11.9 Type 2 diabetes mellitus without complications"],
        providers=["Dr. Priya Verma"],
        medications=["Metformin 500 mg twice daily"],
    )

    payload_lists = summary.as_payload_lists()
    assert payload_lists["Provider Seen"] == ["Patient evaluated in clinic."]
    assert payload_lists["Diagnoses"] == [
        "E11.9 Type 2 diabetes mellitus without complications"
    ]

    pdf_sections = summary.as_pdf_sections()
    headings = [heading for heading, _, _ in pdf_sections]
    assert headings[0] == "Provider Seen"
    assert headings[-1] == "Medications / Prescriptions"
    meds_section = dict((heading, lines) for heading, lines, _ in pdf_sections)
    assert meds_section["Medications / Prescriptions"] == [
        "Metformin 500 mg twice daily"
    ]


def test_formatter_fallbacks_cover_reason_care_plan_and_facility(monkeypatch) -> None:
    aggregated: Dict[str, List[str]] = {
        "provider_seen": [],
        "reason_for_visit": [],
        "clinical_findings": [
            "MRI follow up recommended to evaluate degenerative changes.",
        ],
        "treatment_plan": ["Please contact the clinic if symptoms persist."],
        "healthcare_providers": [
            "Dr. Primary Example",
            "Nurse Practitioner Gomez",
            "Clinic care team",
        ],
        "diagnoses": [
            "E11.9 Type 2 diabetes mellitus without complications",
            "Education provided on nutrition and exercise",
        ],
        "medications": [
            "Medication policy acknowledgement on file",
            "Metformin 500 mg twice daily",
        ],
    }

    class _TestFormatter(CanonicalFormatter):
        def _clean_section_lines(self, lines):
            return [str(item) for item in lines]

        @classmethod
        def _dedupe_ordered(cls, values, **_):
            return [str(val).strip() for val in values if str(val).strip()]

        def _filter_intro_lines(self, lines):
            return list(lines)

        def _filter_details(self, lines):
            return list(lines)

        def _filter_care_plan(self, lines):
            return []

    formatter = _TestFormatter(
        max_overview_lines=2,
        max_clinical_details=3,
        max_care_plan=2,
        min_summary_chars=120,
    )
    formatted = formatter.compose(aggregated, doc_metadata={"facility": "North Clinic"})

    provider_seen = formatted.sections["Provider Seen"]
    assert provider_seen[0].startswith("Facility: North Clinic")
    assert any(line.startswith("Primary provider:") for line in provider_seen[1:2])
    assert any("Supporting team" in line for line in provider_seen)

    reason_lines = formatted.sections["Reason for Visit"]
    assert reason_lines and "MRI follow up recommended" in reason_lines[0]

    care_lines = formatted.sections["Treatment / Follow-up Plan"]
    assert care_lines and "Please contact the clinic" in care_lines[0]

    assert formatted.diagnoses == [
        "E11.9 Type 2 diabetes mellitus without complications"
    ]
    assert formatted.medications == ["Metformin 500 mg twice daily"]
