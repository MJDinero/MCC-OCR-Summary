import re

import pytest

from src.services.summarization import text_utils


def test_prune_admin_text_handles_reason_and_consent():
    assert text_utils.prune_admin_text("REASON FOR VISIT: Follow-up") == "Follow-up"
    assert text_utils.prune_admin_text("I understand that this procedure") == ""
    assert (
        text_utils.prune_admin_text(
            "This is especially important if you are taking diabetes medicines or blood thinners"
        )
        == ""
    )


def test_select_helpers_and_clinical_findings():
    reasons = text_utils.select_reason_statements(
        ["Reason for Visit: Injury check"], ["Reason for Visit - Injury check"], limit=2
    )
    assert reasons == ["Injury check"]
    plan = text_utils.select_plan_statements(
        ["Continue therapy plan", "Continue therapy plan"], limit=2
    )
    assert plan == ["Continue therapy plan"]
    clinical = text_utils.prepare_clinical_findings(
        ["Vitals: BP 120/70 recorded", "Vitals: BP 120/70 recorded"],
        limit=3,
        vitals_summary="BP 120/70",
    )
    assert clinical[0].startswith("BP 120/70")


def test_vitals_detection_and_tables():
    vitals = text_utils.summarize_vitals(
        "BP: 120/70 HR 75 Temp 98.6F Resp Rate 16 SpO2 99%"
    )
    assert (
        vitals == "Vitals: BP 120/70 mmHg, HR 75 bpm, RR 16/min, Temp 98.6°F, SpO2 99%"
    )
    assert text_utils.looks_like_vitals_table("BP 120/80 Pulse 70 Temp 98.7")


def test_signature_and_diagnosis_helpers():
    text = (
        "Respectfully,\n"
        "John Example, M.D.\n"
        "Attending Physician\n"
        "Cervical discopathy noted with chronic neck pain complaints."
    )
    primary, names = text_utils.extract_signature_providers(text)
    assert primary and primary.startswith("Dr. John Example")
    assert "Dr." in names[0]
    diags = text_utils.extract_additional_diagnoses(text)
    assert "Cervical discopathy" in diags


def test_prune_admin_text_empty_and_blank_cases():
    assert text_utils.prune_admin_text("") == ""
    assert text_utils.prune_admin_text("   ") == ""
    assert text_utils.prune_admin_text("Reason for Visit: -") == ""


def test_prepare_clinical_findings_and_vitals_edge_cases():
    clinical = text_utils.prepare_clinical_findings(
        ["", "   ", "Objective tenderness present"],
        limit=2,
        vitals_summary="I understand that there are risks",
    )
    assert clinical == ["Objective tenderness present"]
    assert text_utils.summarize_vitals("") is None
    assert text_utils.summarize_vitals("no vital data here") is None
    assert (
        text_utils.summarize_vitals(
            "Date of Visit: 2026-03-07 Medical Record Number: SYN-20260307"
        )
        is None
    )
    assert not text_utils.looks_like_vitals_table("")


def test_signature_provider_empty_and_non_credential_names():
    assert text_utils.extract_signature_providers("") == (None, [])
    primary, names = text_utils.extract_signature_providers(
        "Respectfully,\nJane Example"
    )
    assert primary is None
    assert names == []


def test_extract_additional_diagnoses_deduplicates_labels(
    monkeypatch: pytest.MonkeyPatch,
):
    duplicate_patterns = (
        (re.compile(r"pain", re.IGNORECASE), "Pain"),
        (re.compile(r"pain", re.IGNORECASE), "Pain"),
    )
    monkeypatch.setattr(text_utils, "_DIAG_PATTERNS", duplicate_patterns)
    assert text_utils.extract_additional_diagnoses("Pain symptoms persist.") == ["Pain"]
    assert text_utils.extract_additional_diagnoses("") == []
