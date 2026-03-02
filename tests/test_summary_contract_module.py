from __future__ import annotations

import pytest

from src.models import summary_contract as contract_module
from src.models.summary_contract import (
    SummaryContract,
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
    claims, _evidence, notice = build_claims_from_sections(
        sections=sections,
        evidence_sources=None,
    )
    assert not claims
    assert notice == "evidence_unavailable"


def test_build_contract_from_text_parses_headings() -> None:
    summary_text = "Provider Seen:\nDr Example\n\nReason for Visit:\n- Follow-up"
    contract = build_contract_from_text(summary_text)
    assert contract.sections[0].title == "Provider Seen"
    assert contract.claims_notice in {
        None,
        "no_evidence_matches",
        "evidence_unavailable",
    }


def test_ensure_contract_dict_handles_legacy_payload() -> None:
    legacy = {
        "Medical Summary": "Provider Seen:\nDr Legacy",
        "_diagnoses_list": "Dx",
    }
    converted = ensure_contract_dict(legacy)
    assert converted["sections"]


def test_best_snippet_handles_empty_inputs() -> None:
    best_snippet = getattr(contract_module, "_best_snippet")
    assert best_snippet("", "source text") is None
    assert best_snippet("statement", "") is None
    assert best_snippet("!!!", "word tokens") is None
    assert best_snippet("statement", "!!!") is None


def test_normalise_sources_skips_missing_text_and_coerces_bad_page() -> None:
    sources = [
        {"text": ""},
        {"text_snippet": "alpha", "page": "oops"},
        {"text": "beta", "pageIndex": 7, "source": "ocr"},
    ]
    normalise_sources = getattr(contract_module, "_normalise_sources")
    normalised = normalise_sources(sources)
    assert len(normalised) == 2
    assert normalised[0]["page"] == 2
    assert normalised[0]["text"] == "alpha"
    assert normalised[1]["page"] == 7
    assert normalised[1]["source"] == "ocr"


def test_build_claims_from_sections_covers_statement_edge_cases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sections = [
        SummarySection(
            slug="diagnoses",
            title="Diagnoses",
            content="-\n•\n- First supported statement\n- Second ignored statement",
            ordinal=1,
            kind="mcc",
        ),
        SummarySection(
            slug="plan",
            title="Plan",
            content="-\n•\n",
            ordinal=2,
            kind="mcc",
        ),
    ]
    sources = [{"page": 1, "text": "First supported statement present in source text"}]
    claims, evidence, notice = build_claims_from_sections(
        sections=sections,
        evidence_sources=sources,
        max_claims=1,
        max_statements_per_section=1,
    )
    assert notice is None
    assert len(claims) == 1
    assert evidence[0].text_snippet

    monkeypatch.setattr(
        contract_module, "_best_snippet", lambda _statement, _source: ("", 0.8)
    )
    claims2, evidence2, notice2 = build_claims_from_sections(
        sections=sections,
        evidence_sources=sources,
    )
    assert not claims2
    assert not evidence2
    assert notice2 == "no_evidence_matches"


def test_ensure_contract_dict_additional_branches() -> None:
    contract = SummaryContract(
        schema_version="test",
        sections=[SummarySection("medical_summary", "Medical Summary", "ok", 1)],
        claims=[],
        evidence_spans=[],
    )
    assert ensure_contract_dict(contract)["sections"]

    mapping_with_sections = {"schema_version": "x", "sections": [], "metadata": {}}
    converted = ensure_contract_dict(mapping_with_sections)
    assert converted["schema_version"] == "x"

    converted_empty = ensure_contract_dict({"foo": "bar"})
    assert converted_empty["sections"] == []

    with pytest.raises(TypeError):
        ensure_contract_dict("invalid")  # type: ignore[arg-type]


def test_build_contract_from_text_handles_empty_input() -> None:
    contract = build_contract_from_text("")
    assert contract.sections[0].slug == "medical_summary"
