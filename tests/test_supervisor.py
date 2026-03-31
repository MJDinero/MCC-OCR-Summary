import importlib
import logging

import pytest

from src.models.summary_contract import SummaryContract, SummarySection
from src.services.summariser_refactored import HeuristicChunkBackend, RefactoredSummariser


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


def _make_contract_summary(
    *,
    provider_seen: str,
    reason_for_visit: str,
    clinical_findings: str,
    treatment_plan: str,
    diagnoses: list[str],
    medications: list[str],
) -> dict[str, object]:
    sections = [
        SummarySection(
            slug="patient_information",
            title="Patient Information",
            content="Not provided",
            ordinal=1,
            kind="context",
        ),
        SummarySection(
            slug="billing_highlights",
            title="Billing Highlights",
            content="Not provided",
            ordinal=2,
            kind="context",
        ),
        SummarySection(
            slug="legal_notes",
            title="Legal / Notes",
            content="Not provided",
            ordinal=3,
            kind="context",
        ),
        SummarySection(
            slug="provider_seen",
            title="Provider Seen",
            content=provider_seen,
            ordinal=4,
            kind="mcc",
            extra={"items": [provider_seen]},
        ),
        SummarySection(
            slug="reason_for_visit",
            title="Reason for Visit",
            content=reason_for_visit,
            ordinal=5,
            kind="mcc",
        ),
        SummarySection(
            slug="clinical_findings",
            title="Clinical Findings",
            content=clinical_findings,
            ordinal=6,
            kind="mcc",
        ),
        SummarySection(
            slug="treatment_follow_up_plan",
            title="Treatment / Follow-up Plan",
            content=treatment_plan,
            ordinal=7,
            kind="mcc",
        ),
        SummarySection(
            slug="diagnoses",
            title="Diagnoses",
            content="\n".join(f"- {item}" for item in diagnoses),
            ordinal=8,
            kind="mcc",
            extra={"items": diagnoses},
        ),
        SummarySection(
            slug="healthcare_providers",
            title="Healthcare Providers",
            content="- Not listed.",
            ordinal=9,
            kind="mcc",
        ),
        SummarySection(
            slug="medications",
            title="Medications / Prescriptions",
            content="\n".join(f"- {item}" for item in medications),
            ordinal=10,
            kind="mcc",
            extra={"items": medications},
        ),
    ]
    return SummaryContract(
        schema_version="2025-10-01",
        sections=sections,
        claims=[],
        evidence_spans=[],
        metadata={"source": "test"},
    ).to_dict()


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


class CapturingSummariser:
    def __init__(self) -> None:
        self.chunk_target_chars = 2500
        self.chunk_hard_max = 3000
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    def summarise(
        self, text: str, *, doc_metadata: dict[str, object] | None = None
    ) -> dict[str, str]:
        self.calls.append((text, doc_metadata))
        return _make_summary(
            (
                "Provider Seen:\nDr. Lane\n\n"
                "Reason for Visit:\nStructured OCR retry input preserved.\n\n"
                "Clinical Findings:\nParagraph boundaries remain available for extraction.\n\n"
                "Treatment / Follow-Up Plan:\nContinue follow-up care.\n\n"
                "Diagnoses:\n- Low back pain\n"
                "Providers:\n- Dr. Lane\n"
                "Medications / Prescriptions:\n- Ibuprofen 400mg"
            ),
            ["Low back pain"],
            ["Dr. Lane"],
            ["Ibuprofen 400mg"],
        )


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
        "- Plan: Continue stretching and heat therapy.\n"
        "No colon line here"
    )
    stripped = supervisor_module._strip_section_headers(text)
    assert "Provider Seen" not in stripped
    assert "Routine wellness exam." in stripped
    assert "Plan: Continue stretching and heat therapy." in stripped
    assert "No colon line here" in stripped
    assert (
        supervisor_module._strip_section_headers("Plain line without colon")
        == "Plain line without colon"
    )
    assert (
        supervisor_module._strip_section_headers("Inline label: preserved content")
        == "Inline label: preserved content"
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


def test_content_alignment_remains_low_for_paraphrastic_summary(supervisor):
    source = (
        "Reason for Visit: Follow-up visit for low back pain after lifting storage boxes at work. "
        "Jordan Carter reports low back pain, lumbar strain, stiffness, and intermittent muscle spasm. "
        "The patient is improving but still has morning stiffness and pain with repeated lifting. "
        "Examination documents lumbar paraspinal tenderness, reduced lumbar range of motion, mild muscle spasm, "
        "normal gait, normal strength, intact sensation, and negative straight leg raise bilaterally. "
        "Plan: Continue ibuprofen 400 mg as needed, cyclobenzaprine 5 mg at bedtime, heating pad, daily stretching "
        "exercises, and modified duty for one week. Follow-up in two weeks for reassessment."
    )
    summary = (
        "Follow-up evaluation after a workplace lifting injury with gradual symptomatic improvement. "
        "Residual lumbar discomfort and muscle tightness persist, but neurological warning signs are absent. "
        "Conservative treatment, activity modification, and reassessment were advised."
    )

    assert supervisor._content_alignment(source, summary) < 0.5


def test_validate_accepts_structured_clean_ocr_summary_with_token_overlap(supervisor):
    source = (
        "Patient Name: Taylor Morgan. Date of Visit: 2026-03-09. "
        "Reason for Visit: Follow-up evaluation for cervical strain, headaches, and right shoulder pain after a rear-end motor vehicle collision. "
        "Symptoms worsen with overhead reaching and prolonged computer work. "
        "History of Present Illness: The patient reports persistent neck tightness, trapezius spasm, and intermittent headaches, "
        "but denies loss of consciousness, new weakness, bowel changes, or bladder changes. "
        "Examination: Cervical paraspinal tenderness, reduced cervical rotation, right trapezius spasm, painful but intact right shoulder range of motion, "
        "normal gait, normal grip strength, and intact sensation in both upper extremities. "
        "Clinical Findings: Spurling test negative. Reflexes symmetric. No focal neurologic deficit. "
        "Imaging Review: Prior cervical radiographs reviewed with no acute fracture identified. "
        "Assessment: Cervical strain with post-traumatic headache and right shoulder strain after motor vehicle collision. "
        "Diagnoses: Cervical strain; right shoulder strain; post-traumatic headache. "
        "Provider Seen: Dr. Elena Ruiz, occupational medicine physician. "
        "Plan: Continue home stretching, heat, ibuprofen 400 mg as needed, and physical therapy twice weekly for four weeks. "
        "Follow-up: Return in two weeks for reassessment of neck pain, headache frequency, and work restrictions."
    )
    summary = _make_contract_summary(
        provider_seen="Dr. Elena Ruiz",
        reason_for_visit=(
            "Follow-up evaluation for cervical strain, headaches, and right shoulder pain "
            "after a rear-end motor vehicle collision."
        ),
        clinical_findings=(
            "Persistent neck tightness, trapezius spasm, intermittent headaches, cervical "
            "paraspinal tenderness, reduced cervical rotation, and no focal neurologic deficit."
        ),
        treatment_plan=(
            "Continue home stretching, heat, ibuprofen 400 mg as needed, physical therapy "
            "twice weekly for four weeks, and return in two weeks for reassessment."
        ),
        diagnoses=[
            "Cervical strain",
            "Right shoulder strain",
            "Post-traumatic headache",
        ],
        medications=["Ibuprofen 400 mg as needed"],
    )

    doc_stats = supervisor.collect_doc_stats(
        text=source,
        pages=[{"page_number": idx, "text": f"page-{idx}"} for idx in range(1, 5)],
        file_bytes=None,
    )
    validation = supervisor.validate(ocr_text=source, summary=summary, doc_stats=doc_stats)

    assert validation["supervisor_passed"]
    assert validation["checks"]["alignment_ok"]
    assert validation["content_alignment"] >= 0.8


def test_retry_variant_preserves_structured_ocr_text(supervisor):
    summariser = CapturingSummariser()
    text = (
        "Reason for Visit: Follow-up for lumbar strain.\n\n"
        "Assessment: Symptoms improving with conservative care.\n\n"
        "Plan: Continue ibuprofen and modified duty."
    )

    supervisor._invoke_variant(
        summariser,
        text,
        {"name": "chunk-tight", "chunk_target": 2000, "chunk_max": 2600},
        doc_metadata={"job_id": "job-123"},
    )

    assert len(summariser.calls) == 1
    seen_text, seen_metadata = summariser.calls[0]
    assert seen_text == text
    assert "\n\n" in seen_text
    assert seen_metadata == {"job_id": "job-123"}


def test_validate_passes_inline_label_summary_without_lowering_threshold(supervisor):
    text = (
        "MCC OCR Smoke Test - NON-PHI Fictional Clinical Follow-Up\n\n"
        "Generated UTC: 2026-03-07T20-04-35Z\n\n"
        "Reason for Visit: Follow-up visit for low back pain after lifting storage boxes at work. "
        "The fictional patient reports low back pain, lumbar strain, stiffness, and intermittent muscle spasm. "
        "Symptoms improve with rest, gentle stretching, and heat, and worsen after bending, lifting, or prolonged standing.\n\n"
        "History and Findings: The fictional clinic note says the patient is improving but still has morning stiffness and pain with repeated lifting. "
        "Examination documents lumbar paraspinal tenderness, reduced lumbar range of motion, mild muscle spasm, normal gait, normal strength, intact sensation, and no bowel or bladder changes. "
        "Straight leg raise is negative bilaterally.\n\n"
        "Assessment: Findings remain consistent with low back pain and lumbar strain without focal neurologic deficit.\n\n"
        "Plan: Continue ibuprofen 400 mg as needed, cyclobenzaprine 5 mg at bedtime, heating pad, daily stretching exercises, and modified duty for one week. "
        "Follow-up in two weeks for reassessment of low back pain and lumbar strain."
    )
    pages = [{"page_number": 1, "text": text}]
    doc_stats = supervisor.collect_doc_stats(text=text, pages=pages, file_bytes=None)
    summary = RefactoredSummariser(backend=HeuristicChunkBackend()).summarise(text)

    validation = supervisor.validate(ocr_text=text, summary=summary, doc_stats=doc_stats)

    assert validation["supervisor_passed"]
    assert validation["content_alignment"] >= 0.8
    assert validation["checks"]["alignment_ok"]


def test_validate_uses_filtered_alignment_source_for_page_filtered_ocr(supervisor):
    clinical = (
        "Follow-up evaluation for lumbar strain after a workplace lifting injury. "
        "The patient reports persistent low back pain with muscle spasm and stiffness. "
        "Physical exam documents lumbar paraspinal tenderness, reduced range of motion, and normal gait. "
        "Plan is to continue ibuprofen 400 mg, home stretching, and return in two weeks."
    )
    admin = (
        "I understand that the following care/procedure does not guarantee result or cure. "
        "Please fill your prescriptions and take all medications as directed. "
        "Call 911 if symptoms worsen and return to the emergency room. "
        "Authorization to disclose protected health information. "
    )
    raw_text = " ".join(([clinical] * 8) + ([admin] * 40))
    filtered_text = " ".join([clinical] * 8)
    doc_stats = supervisor.collect_doc_stats(
        text=raw_text,
        pages=[{"page_number": idx, "text": raw_text} for idx in range(1, 80)],
        file_bytes=None,
    )
    summary = _make_contract_summary(
        provider_seen="Dr. Elena Ruiz",
        reason_for_visit=(
            "Follow-up evaluation for lumbar strain after a workplace lifting injury "
            "with persistent low back pain and stiffness."
        ),
        clinical_findings=(
            "Lumbar paraspinal tenderness, reduced lumbar range of motion, muscle "
            "spasm, and normal gait were documented on exam."
        ),
        treatment_plan=(
            "Continue ibuprofen 400 mg, home stretching, modified duty, and return "
            "in two weeks for reassessment."
        ),
        diagnoses=["Lumbar strain", "Low back pain", "Muscle spasm"],
        medications=["Ibuprofen 400 mg as needed"],
    )

    baseline_validation = supervisor.validate(
        ocr_text=raw_text,
        summary=summary,
        doc_stats=doc_stats,
    )
    filtered_validation = supervisor.validate(
        ocr_text=raw_text,
        alignment_source_text=filtered_text,
        summary=summary,
        doc_stats=doc_stats,
    )

    assert not baseline_validation["supervisor_passed"]
    assert baseline_validation["reason"] == "content_alignment_low"
    assert baseline_validation["content_alignment"] < 0.8
    assert filtered_validation["supervisor_passed"]
    assert filtered_validation["content_alignment"] >= 0.8


def test_validate_logs_alignment_metrics(caplog, supervisor):
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

    with caplog.at_level(logging.INFO, logger="supervisor"):
        validation = supervisor.validate(ocr_text=text, summary=summary, doc_stats=doc_stats)

    assert validation["supervisor_passed"]
    metric_record = next(
        record
        for record in caplog.records
        if record.name == "supervisor" and record.message == "supervisor_alignment_metrics"
    )
    assert metric_record.content_alignment >= 0.8
    assert metric_record.source_paragraphs >= 1
    assert metric_record.summary_paragraphs >= 1
    assert metric_record.source_tokens > 0
    assert metric_record.summary_tokens > 0


def test_simple_supervisor_rejects_boilerplate_dominant_structured_summary(
    supervisor_module,
):
    simple_supervisor = supervisor_module.CommonSenseSupervisor(simple=True)
    ocr_text = (
        "Back pain follow-up after lifting injury with lumbar strain. "
        "Exam showed tenderness and ibuprofen was continued. "
    ) * 40
    doc_stats = simple_supervisor.collect_doc_stats(
        text=ocr_text, pages=[{"page_number": 1, "text": ocr_text}], file_bytes=None
    )
    summary = _make_contract_summary(
        provider_seen="Dr. Michael Davis, M.D.",
        reason_for_visit=(
            "- Authorize my physicians to use their professional judgment.\n"
            "- PLEASE FILL YOUR PRESCRIPTIONS AND TAKE ALL MEDICATIONS AS DIRECTED.\n"
            "- Nurse Review: Not Reviewed Doctor Cosign: Not Required."
        ),
        clinical_findings=(
            "- I understand this care/procedure(s) does not guarantee result or a cure.\n"
            "- Lumbar tenderness was documented on exam."
        ),
        treatment_plan=(
            "- Order Status: Discontinued.\n"
            "- Follow-up appointment scheduled."
        ),
        diagnoses=[
            "Check your injection site every day for signs of infection.",
            "Back pain, Hand/finger pain/injury, Ankle pain/injury",
        ],
        medications=[
            "Keep an active list of medications available so that you can share with other providers.",
            "Please fill your prescriptions and take all medications as directed.",
        ],
    )

    validation = simple_supervisor.validate(
        ocr_text=ocr_text, summary=summary, doc_stats=doc_stats
    )

    assert not validation["supervisor_passed"]
    assert not validation["checks"]["quality_ok"]
    assert "summary_quality_low" in validation["reason"]
    assert "boilerplate_dominant_sections" in validation["quality"]["reasons"]
    assert "reason_for_visit" in validation["quality"]["mixed_sections"]
    assert "medications" in validation["quality"]["mixed_sections"]


def test_simple_supervisor_rejects_mixed_provider_and_visit_fields(supervisor_module):
    simple_supervisor = supervisor_module.CommonSenseSupervisor(simple=True)
    ocr_text = (
        "Follow-up visit for low back pain after lifting injury. "
        "Provider documented lumbar strain and continued ibuprofen. "
    ) * 30
    doc_stats = simple_supervisor.collect_doc_stats(
        text=ocr_text, pages=[{"page_number": 1, "text": ocr_text}], file_bytes=None
    )
    summary = _make_contract_summary(
        provider_seen=(
            "Dr. Lane discussed ibuprofen 400 mg, return precautions, and discharge instructions."
        ),
        reason_for_visit=(
            "Doctor Cosign: Not Required. Order Status: Discontinued. "
            "Please fill your prescriptions and take all medications as directed."
        ),
        clinical_findings="Lumbar paraspinal tenderness with reduced range of motion.",
        treatment_plan="Continue ibuprofen 400 mg and follow up in two weeks.",
        diagnoses=["Lumbar strain"],
        medications=["Ibuprofen 400 mg as needed"],
    )

    validation = simple_supervisor.validate(
        ocr_text=ocr_text, summary=summary, doc_stats=doc_stats
    )

    assert not validation["supervisor_passed"]
    assert "mixed_section_content" in validation["quality"]["reasons"]
    assert set(validation["quality"]["mixed_sections"]) >= {
        "provider_seen",
        "reason_for_visit",
    }


def test_simple_supervisor_accepts_clean_structured_summary(supervisor_module):
    simple_supervisor = supervisor_module.CommonSenseSupervisor(simple=True)
    ocr_text = (
        "Follow-up visit for lumbar strain after lifting injury. "
        "Exam showed lumbar tenderness and reduced range of motion. "
        "Continue ibuprofen 400 mg as needed and return in two weeks. "
    ) * 20
    doc_stats = simple_supervisor.collect_doc_stats(
        text=ocr_text, pages=[{"page_number": 1, "text": ocr_text}], file_bytes=None
    )
    summary = _make_contract_summary(
        provider_seen="Dr. Lane",
        reason_for_visit="Follow-up visit for lumbar strain after lifting injury.",
        clinical_findings="Lumbar tenderness and reduced range of motion were documented on exam.",
        treatment_plan="Continue ibuprofen 400 mg as needed and return in two weeks.",
        diagnoses=["Lumbar strain"],
        medications=["Ibuprofen 400 mg as needed"],
    )

    validation = simple_supervisor.validate(
        ocr_text=ocr_text, summary=summary, doc_stats=doc_stats
    )

    assert validation["supervisor_passed"]
    assert validation["checks"]["quality_ok"]


def test_validate_handles_zero_target_length(supervisor_module):
    zero_supervisor = supervisor_module.CommonSenseSupervisor(
        baseline_min_chars=0, multi_pass_min_chars=0
    )
    doc_stats = zero_supervisor.collect_doc_stats(text="", pages=[], file_bytes=None)
    validation = zero_supervisor.validate(ocr_text="", summary={}, doc_stats=doc_stats)
    assert validation["length_score"] == 1.0
    assert not validation["supervisor_passed"]
