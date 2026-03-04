from __future__ import annotations

from src.services.summarization import formatter as summary_formatter

REQUIRED_HEADINGS = [
    "Provider Seen",
    "Reason for Visit",
    "Clinical Findings",
    "Treatment / Follow-up Plan",
    "Diagnoses",
    "Healthcare Providers",
    "Medications / Prescriptions",
]


def test_build_mcc_bible_sections_filters_blank_lines() -> None:
    sections = summary_formatter.build_mcc_bible_sections(
        chunk_count=4,
        facility="Riverside Clinic",
        provider_seen="Dr. Rivera",
        reason_lines=["", "Chronic lumbar pain follow-up", "  "],
        clinical_findings=["  ", "Paraspinal tenderness noted", ""],
        care_plan=["Continue PT twice weekly"],
        diagnoses=["M54.16 Lumbar radiculopathy"],
        healthcare_providers=["PT Team", ""],
        medications=["Gabapentin 300 mg nightly", ""],
    )

    section_map = dict(sections)
    assert section_map["Reason for Visit"] == "- Chronic lumbar pain follow-up"
    assert section_map["Clinical Findings"] == "- Paraspinal tenderness noted"
    assert "Facility: Riverside Clinic" in section_map["Provider Seen"]


def test_build_mcc_bible_summary_keeps_required_headings() -> None:
    summary_text = summary_formatter.build_mcc_bible_summary(
        chunk_count=1,
        facility=None,
        provider_seen=None,
        reason_lines=[],
        clinical_findings=[],
        care_plan=[],
        diagnoses=[],
        healthcare_providers=[],
        medications=[],
    )

    for heading in REQUIRED_HEADINGS:
        assert f"{heading}:" in summary_text
    assert "Provider not documented." in summary_text
    assert "No diagnoses documented." in summary_text
