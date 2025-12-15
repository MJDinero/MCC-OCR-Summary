from __future__ import annotations

from src.models.summary_contract import (
    SummarySection,
    build_claims_from_sections,
    build_contract_from_text,
    ensure_contract_dict,
)


def test_build_claims_from_sections_matches_substrings() -> None:
    sections = [
        SummarySection(
            slug="diagnoses",
            title="Diagnoses",
            content="- Hypertension",
            ordinal=1,
            kind="mcc",
        )
    ]
    sources = [{"page": 1, "text": "Patient diagnosed with hypertension"}]
    claims, evidence, notice = build_claims_from_sections(
        sections=sections,
        evidence_sources=sources,
        max_claims=2,
    )
    assert not notice
    assert claims[0].value.startswith("Hypertension")
    assert evidence[0].page == 1


def test_build_claims_from_sections_token_overlap() -> None:
    sections = [
        SummarySection(
            slug="care_plan",
            title="Plan",
            content="- Continue ACE inhibitor",
            ordinal=1,
            kind="mcc",
        )
    ]
    sources = [{"page": 2, "text": "Plan discussed to continue ACE inhibitor therapy."}]
    claims, evidence, notice = build_claims_from_sections(
        sections=sections,
        evidence_sources=sources,
        max_claims=2,
    )
    assert not notice
    assert claims and evidence


def test_build_claims_from_sections_handles_missing_sources() -> None:
    sections = [
        SummarySection(
            slug="diagnoses",
            title="Diagnoses",
            content="- Item",
            ordinal=1,
            kind="mcc",
        )
    ]
    claims, evidence, notice = build_claims_from_sections(
        sections=sections,
        evidence_sources=None,
    )
    assert not claims
    assert notice == "evidence_unavailable"


def test_build_contract_from_text_parses_headings() -> None:
    summary_text = "Provider Seen:\nDr Example\n\nReason for Visit:\n- Follow-up"
    contract = build_contract_from_text(summary_text)
    assert contract.sections[0].title == "Provider Seen"
    assert contract.claims_notice in {None, "no_evidence_matches", "evidence_unavailable"}


def test_ensure_contract_dict_handles_legacy_payload() -> None:
    legacy = {
        "Medical Summary": "Provider Seen:\nDr Legacy",
        "_diagnoses_list": "Dx",
    }
    converted = ensure_contract_dict(legacy)
    assert converted["sections"]
