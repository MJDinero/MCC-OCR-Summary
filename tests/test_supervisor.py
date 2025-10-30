import importlib

import pytest


def _make_summary(
    body: str, diagnoses: list[str], providers: list[str], meds: list[str]
) -> dict[str, str]:
    return {
        "Patient Information": "N/A",
        "Medical Summary": body,
        "Billing Highlights": "N/A",
        "Legal / Notes": "N/A",
        "_diagnoses_list": "\n".join(diagnoses),
        "_providers_list": "\n".join(providers),
        "_medications_list": "\n".join(meds),
    }


@pytest.fixture
def supervisor_module():
    module = importlib.import_module("src.services.supervisor")
    importlib.reload(module)
    return module


@pytest.fixture
def supervisor(supervisor_module):
    return supervisor_module.CommonSenseSupervisor()


class SequenceSummariser:
    """Stub summariser that returns pre-seeded outputs per call."""

    def __init__(self, outputs: list[dict[str, str]]):
        self.outputs = outputs
        self.calls = 0
        self.chunk_target_chars = 2500
        self.chunk_hard_max = 3000

    def summarise(self, text: str) -> dict[str, str]:
        index = min(self.calls, len(self.outputs) - 1)
        result = self.outputs[index]
        self.calls += 1
        return result


def test_supervisor_retry_merges_high_volume_document(supervisor_module, supervisor):
    source_text = (
        "Hypertension management plan requires lifestyle adjustments and ongoing monitoring. "
        * 3500
    )
    pages = [
        {"page_number": i, "text": "Hypertension management plan"}
        for i in range(1, 251)
    ]
    file_bytes = b"x" * (12 * 1024 * 1024)
    doc_stats = supervisor.collect_doc_stats(
        text=source_text, pages=pages, file_bytes=file_bytes
    )

    short_body = (
        "Provider Seen:\nDr. Patel\n\n"
        "Reason for Visit:\nHypertension management plan review.\n\n"
        "Clinical Findings:\nBrief overview only.\n\n"
        "Treatment / Follow-Up Plan:\nContinue regimen.\n\n"
        "Diagnoses:\n- Essential hypertension\n"
        "Providers:\n- Dr. Patel\n"
        "Medications / Prescriptions:\n- Lisinopril"
    )
    medium_body = (
        "Provider Seen:\nDr. Patel\n\n"
        "Reason for Visit:\nHypertension management plan review with limited details.\n\n"
        "Clinical Findings:\nStable readings reported.\n\n"
        "Treatment / Follow-Up Plan:\nContinue therapy and monitor.\n\n"
        "Additional Notes: missing structured lists"
    )
    source_sentence = "Hypertension management plan requires lifestyle adjustments and ongoing monitoring."
    repeated = " ".join([source_sentence] * 9)
    final_body = (
        "Provider Seen:\nDr. Patel, cardiology specialist guiding the hypertension management plan.\n\n"
        f"Reason for Visit:\n{repeated}\n\n"
        f"Clinical Findings:\n{repeated}\n\n"
        f"Treatment / Follow-Up Plan:\n{repeated}\n\n"
        f"Diagnoses:\n- {source_sentence}\n- Chronic kidney disease stage 2 monitored during hypertension management\n"
        "Providers:\n- Dr. Patel\n- Nurse Educator Maria Lopez\n"
        "Medications / Prescriptions:\n- Lisinopril 40mg\n- Hydrochlorothiazide 25mg\n"
    )

    outputs = [
        _make_summary(
            short_body, ["Essential hypertension"], ["Dr. Patel"], ["Lisinopril"]
        ),
        _make_summary(medium_body, [], [], []),
        _make_summary(
            final_body,
            [
                "Essential hypertension with longstanding cardiovascular risk factors",
                "Chronic kidney disease stage 2",
            ],
            ["Dr. Patel", "Nurse Educator Maria Lopez"],
            ["Lisinopril 40mg", "Hydrochlorothiazide 25mg", "Omega-3 supplement"],
        ),
    ]
    summariser = SequenceSummariser(outputs)

    initial_summary = summariser.summarise(source_text)
    initial_validation = supervisor.validate(
        ocr_text=source_text,
        summary=initial_summary,
        doc_stats=doc_stats,
    )
    assert not initial_validation["supervisor_passed"]
    assert initial_validation["checks"]["multi_pass_required"]

    result = supervisor.retry_and_merge(
        summariser=summariser,
        ocr_text=source_text,
        doc_stats=doc_stats,
        initial_summary=initial_summary,
        initial_validation=initial_validation,
    )

    assert result.validation["supervisor_passed"]
    assert result.validation["retries"] == 2
    assert result.validation["checks"]["length_ok"]
    assert result.validation["checks"]["structure_ok"]
    assert result.validation["checks"]["alignment_ok"]
    assert result.validation["doc_stats"]["pages"] == 250
    assert result.validation["length_score"] >= 0.75
    assert result.validation["content_alignment"] >= 0.8
    assert summariser.calls == 3


def test_supervisor_pass_small_document_without_retry(supervisor):
    text = "Hypertension follow-up note discussing medication adherence." * 10
    pages = [{"page_number": 1, "text": text}]
    doc_stats = supervisor.collect_doc_stats(
        text=text, pages=pages, file_bytes=b"x" * 2048
    )
    body = (
        "Provider Seen:\nDr. Lane\n\n"
        "Reason for Visit:\nHypertension follow-up note discussing medication adherence and reinforcing routine monitoring.\n\n"
        "Clinical Findings:\nBlood pressure improving with current therapy and adherence conversations documented for the hypertension follow-up note discussing medication adherence.\n\n"
        "Treatment / Follow-Up Plan:\nMaintain regimen, track home readings, reinforce sodium reduction, and continue the hypertension follow-up note discussing medication adherence with motivational interviewing.\n\n"
        "Diagnoses:\n- Essential hypertension\n"
        "Providers:\n- Dr. Lane\n"
        "Medications / Prescriptions:\n- Losartan 50mg"
    )
    summary = _make_summary(
        body, ["Essential hypertension"], ["Dr. Lane"], ["Losartan 50mg"]
    )
    validation = supervisor.validate(
        ocr_text=text, summary=summary, doc_stats=doc_stats
    )
    assert validation["supervisor_passed"]
    assert validation["retries"] == 0
    assert validation["checks"]["structure_ok"]


def test_strip_section_headers_and_alignment_shortcuts(supervisor_module, supervisor):
    text = (
        "Provider Seen:\nDr. Example\n\n"
        "\n"
        "Reason for Visit:\nRoutine wellness exam.\n"
        "No colon line here"
    )
    stripped = supervisor_module._strip_section_headers(text)
    assert "Provider Seen" not in stripped
    assert "Routine wellness exam." in stripped
    assert "No colon line here" in stripped
    assert (
        supervisor_module._strip_section_headers("Plain line without colon")
        == "Plain line without colon"
    )

    assert supervisor._content_alignment("", stripped) == 0.0


def test_normalise_mb_and_extract_summary_text(supervisor_module):
    assert supervisor_module._normalise_mb(None) == 0.0
    assert supervisor_module._normalise_mb(2 * 1024 * 1024) == 2.0
    joined = supervisor_module._extract_summary_text(
        {"Other": "Alpha", "Notes": "Beta"}
    )
    assert "Alpha" in joined and "Beta" in joined
    assert (
        supervisor_module._extract_summary_text(
            {"Medical Summary": "Primary narrative", "Other": "Beta"}
        )
        == "Primary narrative"
    )
    assert supervisor_module._extract_summary_text({}) == ""


def test_retry_and_merge_returns_initial_when_already_valid(supervisor):
    text = "Hypertension follow-up note discussing medication adherence." * 12
    pages = [{"page_number": 1, "text": text}]
    doc_stats = supervisor.collect_doc_stats(
        text=text, pages=pages, file_bytes=b"x" * 1024
    )
    body = (
        "Provider Seen:\nDr. Lane\n\n"
        "Reason for Visit:\nHypertension follow-up note discussing medication adherence in depth.\n\n"
        "Clinical Findings:\nDetailed chart review with hypertension follow-up note discussing medication adherence highlights.\n\n"
        "Treatment / Follow-Up Plan:\nContinue therapy with counseling and documented medication adherence conversations.\n\n"
        "Diagnoses:\n- Essential hypertension\n"
        "Providers:\n- Dr. Lane\n"
        "Medications / Prescriptions:\n- Losartan 50mg\n- Amlodipine 5mg"
    )
    outputs = [
        _make_summary(
            body,
            ["Essential hypertension"],
            ["Dr. Lane"],
            ["Losartan 50mg", "Amlodipine 5mg"],
        )
    ]
    summariser = SequenceSummariser(outputs)
    initial_summary = summariser.summarise(text)
    initial_validation = supervisor.validate(
        ocr_text=text, summary=initial_summary, doc_stats=doc_stats
    )
    assert initial_validation["supervisor_passed"]

    result = supervisor.retry_and_merge(
        summariser=summariser,
        ocr_text=text,
        doc_stats=doc_stats,
        initial_summary=initial_summary,
        initial_validation=initial_validation,
    )

    assert result.validation["supervisor_passed"]
    assert result.validation["retries"] == 0
    assert summariser.calls == 1


def test_structured_list_detection_with_metadata_fields(supervisor):
    summary = {
        "Medical Summary": "Provider Seen: N/A",
        "_diagnoses_list": "Condition A\nCondition B",
        "_providers_list": "",
        "_medications_list": "",
    }
    assert supervisor._has_structured_list(summary["Medical Summary"], summary)
    assert supervisor._has_structured_list("1. First\n2. Second", {})


def test_tokenization_filters_stopwords_and_digits(supervisor):
    tokens = list(
        supervisor._tokenize(
            "The patient 123 presented with hypertension follow up plan."
        )
    )
    assert "patient" not in tokens
    assert "123" not in tokens
    assert "hypertension" in tokens


def test_paragraph_and_header_counts(supervisor):
    assert supervisor._count_paragraphs("Single paragraph without breaks.") == 1
    assert supervisor._count_paragraphs("") == 0
    headers_text = (
        "Diagnoses: Hypertension\nProvider Seen: Dr. A\nMedications: Lisinopril"
    )
    assert supervisor._count_headers(headers_text) >= 3


def test_content_alignment_when_tokens_filtered(supervisor):
    assert supervisor._content_alignment("the and of", "the and of") == 0.0


def test_validate_handles_zero_target_length(supervisor_module):
    zero_supervisor = supervisor_module.CommonSenseSupervisor(
        baseline_min_chars=0, multi_pass_min_chars=0
    )
    doc_stats = zero_supervisor.collect_doc_stats(text="", pages=[], file_bytes=None)
    validation = zero_supervisor.validate(ocr_text="", summary={}, doc_stats=doc_stats)
    assert validation["length_score"] == 1.0
    assert not validation["supervisor_passed"]
