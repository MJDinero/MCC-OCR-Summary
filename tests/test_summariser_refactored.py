from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict

import pytest

from src.errors import SummarizationError
from src.models.summary_contract import SummaryContract
from src.services.summariser_refactored import (
    AdaptiveSummariser,
    ChunkSummaryBackend,
    HeuristicChunkBackend,
    OpenAIOneShotResponsesBackend,
    OpenAIResponsesBackend,
    RefactoredSummariser,
    _prepare_summary_source,
    _count_text_paragraphs,
    _cli,
    _load_input_payload,
    _merge_dicts,
    _select_summary_route,
    _split_gcs_uri,
)
from src.services.supervisor import CommonSenseSupervisor


@dataclass
class StubBackend(ChunkSummaryBackend):
    responses: Dict[int, Dict[str, Any]]

    def summarise_chunk(
        self,
        *,
        chunk_text: str,
        chunk_index: int,
        total_chunks: int,
        estimated_tokens: int,
    ) -> Dict[str, Any]:
        payload = self.responses.get(chunk_index)
        if not payload:
            fallback_index = max(self.responses)
            payload = self.responses[fallback_index]
        # Include a snippet of the chunk to ensure backend sees the same data across invocations.
        result = dict(payload)
        result.setdefault(
            "overview", f"Chunk {chunk_index} covers {len(chunk_text.split())} words."
        )
        return result


@dataclass
class MarkerAwareBackend(ChunkSummaryBackend):
    calls: int = 0

    def summarise_chunk(
        self,
        *,
        chunk_text: str,
        chunk_index: int,
        total_chunks: int,
        estimated_tokens: int,
    ) -> Dict[str, Any]:
        _ = total_chunks, estimated_tokens
        self.calls += 1
        diagnoses = re.findall(r"DX-\d{2}", chunk_text)
        providers = re.findall(r"Dr\. Provider\d+", chunk_text)
        medications = re.findall(r"Med-\d+", chunk_text)
        phases = re.findall(r"phase \d+", chunk_text)

        diagnosis = diagnoses[0] if diagnoses else f"DX-{chunk_index + 1:02d}"
        provider = providers[0] if providers else f"Dr. Provider{chunk_index + 1}"
        medication = medications[0] if medications else f"Med-{chunk_index + 1}"
        phase = phases[0] if phases else f"phase {chunk_index + 1}"

        return {
            "overview": (
                f"Encounter progression references {diagnosis} with persistent lumbar pain."
            ),
            "key_points": [
                f"Patient continues to report lumbar pain associated with {diagnosis}."
            ],
            "clinical_details": [
                f"Objective exam supports diagnosis {dx}."
                for dx in (diagnoses[:2] or [diagnosis])
            ],
            "care_plan": [
                f"Continue physical therapy {phase} and reassess in follow-up."
            ],
            "diagnoses": [f"{diagnosis} Lumbar radiculopathy"],
            "providers": [provider],
            "medications": [f"{medication} nightly"],
        }


@dataclass
class StubOneShotBackend:
    payload: Dict[str, Any]
    calls: int = 0
    error: str | None = None

    def summarise_document(
        self,
        *,
        document_text: str,
        estimated_tokens: int,
        page_count: int,
        routing_metrics: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        _ = document_text, estimated_tokens, page_count, routing_metrics
        self.calls += 1
        if self.error:
            raise SummarizationError(self.error)
        return dict(self.payload)


def _build_large_ocr_like_payload(
    page_count: int = 8,
) -> tuple[str, list[dict[str, Any]]]:
    pages: list[dict[str, Any]] = []
    parts: list[str] = []
    for page_number in range(1, page_count + 1):
        segment = (
            f"Page {page_number} encounter at Riverside Clinic by Dr. Provider{page_number}. "
            f"Patient reports symptom cluster {page_number}: severe lumbar pain with radiculopathy. "
            f"Assessment indicates diagnosis code DX-{page_number:02d}. "
            f"Treatment plan includes physical therapy phase {page_number} and medication Med-{page_number}. "
            f"Follow-up in {page_number + 1} weeks. "
        )
        pages.append({"page_number": page_number, "text": segment})
        parts.append(segment * 5)
    return " ".join(parts), pages


def test_refactored_summary_structure_and_length() -> None:
    text = (
        "Patient Jane Doe visited the clinic complaining of persistent migraines. "
        "Neurological examination revealed no focal deficits. MRI performed in March 2024 was normal. "
        "Blood pressure measured at 138/88 mmHg. Provider Dr. Alicia Carter discussed lifestyle modifications. "
        "Sumatriptan 50 mg prescribed to be taken at onset of migraine. Follow-up arranged in 6 weeks. "
        "Patient also reports mild anxiety managed with cognitive behavioural therapy."
    ) * 5

    backend = StubBackend(
        responses={
            1: {
                "overview": "Follow-up neurology visit for chronic migraines without aura.",
                "key_points": [
                    "Patient reports increased migraine frequency impacting daily activities.",
                    "Normal neurological examination with stable vitals.",
                ],
                "clinical_details": [
                    "Blood pressure 138/88 mmHg; neurological exam non-focal.",
                    "MRI from March 2024 reviewed and remains normal.",
                ],
                "care_plan": [
                    "Continue sumatriptan 50 mg at migraine onset and track response.",
                    "Reinforce hydration, sleep hygiene, and headache diary usage.",
                ],
                "diagnoses": ["G43.709 Chronic migraine without aura"],
                "providers": ["Dr. Alicia Carter"],
                "medications": ["Sumatriptan 50 mg as needed"],
            },
            2: {
                "key_points": [
                    "Patient engaging in cognitive behavioural therapy for anxiety symptoms.",
                ],
                "clinical_details": [
                    "Patient denies visual changes, motor weakness, or speech disturbance.",
                    "Reports mild anxiety managed through behavioural interventions.",
                ],
                "care_plan": [
                    "Schedule follow-up neurology visit in 6 weeks to reassess frequency and treatment response.",
                ],
                "diagnoses": ["F41.9 Anxiety disorder, unspecified"],
                "providers": ["Clinic behavioural health team"],
                "medications": ["Cognitive behavioural therapy"],
            },
        }
    )

    summariser = RefactoredSummariser(
        backend=backend, target_chars=300, max_chars=420, overlap_chars=60
    )
    summary = summariser.summarise(text, doc_metadata={"facility": "MCC Neurology"})
    contract = SummaryContract.from_mapping(summary)
    medical_summary = contract.as_text()

    assert "Provider Seen:" in medical_summary
    assert "Reason for Visit:" in medical_summary
    assert "Clinical Findings:" in medical_summary
    assert "Treatment / Follow-up Plan:" in medical_summary
    assert len(medical_summary) >= summariser.min_summary_chars

    diagnoses_section = next(
        section for section in contract.sections if section.slug == "diagnoses"
    )
    diagnoses_items = diagnoses_section.extra.get("items", [])
    assert (
        diagnoses_items
        and "G43.709 Chronic migraine without aura" in diagnoses_items[0]
    )
    providers_section = next(
        section
        for section in contract.sections
        if section.slug == "healthcare_providers"
    )
    providers_items = providers_section.extra.get("items", [])
    assert providers_items and providers_items[0].startswith("Dr. Alicia Carter")


def test_refactored_summary_requires_non_empty_text() -> None:
    backend = StubBackend(responses={1: {"overview": "Empty"}})
    summariser = RefactoredSummariser(backend=backend)
    with pytest.raises(SummarizationError):
        summariser.summarise("   ")


def test_summariser_logs_section_detection_metrics(caplog) -> None:
    text = (
        "Patient Jane Doe visited the clinic complaining of persistent migraines.\n\n"
        "Neurological examination revealed no focal deficits and blood pressure measured at 138/88 mmHg.\n\n"
        "Provider Dr. Alicia Carter discussed lifestyle modifications and prescribed Sumatriptan 50 mg."
    ) * 3
    backend = StubBackend(
        responses={
            1: {
                "overview": "Follow-up neurology visit for chronic migraines without aura.",
                "key_points": [
                    "Patient reports increased migraine frequency impacting daily activities."
                ],
                "clinical_details": [
                    "Blood pressure 138/88 mmHg; neurological exam non-focal."
                ],
                "care_plan": ["Continue sumatriptan 50 mg at migraine onset."],
                "diagnoses": ["G43.709 Chronic migraine without aura"],
                "providers": ["Dr. Alicia Carter"],
                "medications": ["Sumatriptan 50 mg as needed"],
            }
        }
    )
    summariser = RefactoredSummariser(backend=backend, min_summary_chars=320)

    with caplog.at_level(logging.INFO, logger="summariser.refactored"):
        summariser.summarise(text, doc_metadata={"facility": "MCC Neurology"})

    record = next(
        item
        for item in caplog.records
        if item.name == "summariser.refactored"
        and item.message == "summariser_section_detection"
    )
    assert record.input_paragraphs >= 3
    assert record.input_tokens > 0
    assert record.overview_lines >= 1
    assert record.key_points_count >= 1
    assert record.care_plan_count >= 1
    assert record.diagnoses_count >= 1
    assert record.providers_count >= 1


def test_compose_summary_pads_short_outputs() -> None:
    backend = StubBackend(
        responses={
            1: {
                "overview": "Brief visit for vaccination update.",
                "key_points": ["Tdap booster administered."],
                "clinical_details": ["No adverse reactions documented."],
                "care_plan": ["Monitor for injection site soreness."],
            }
        }
    )
    summariser = RefactoredSummariser(backend=backend, min_summary_chars=300)
    result = summariser.summarise("Tdap booster provided.")
    assert len(SummaryContract.from_mapping(result).as_text()) >= 300


def test_overview_without_patient_token_is_preserved() -> None:
    backend = StubBackend(
        responses={
            1: {
                "overview": "Follow-up neurology visit for chronic migraines without aura.",
                "key_points": [],
                "clinical_details": ["MRI from March 2024 remains normal."],
                "care_plan": ["Continue headache diary tracking."],
                "diagnoses": ["G43.709 Chronic migraine without aura"],
                "providers": ["Dr. Alicia Carter"],
                "medications": ["Sumatriptan 50 mg as needed"],
            }
        }
    )
    summariser = RefactoredSummariser(backend=backend, min_summary_chars=300)
    contract = SummaryContract.from_mapping(
        summariser.summarise(
            "Neurology follow-up for migraine management with stable imaging."
        )
    )

    reason_section = next(
        section for section in contract.sections if section.slug == "reason_for_visit"
    )
    assert "Follow-up neurology visit for chronic migraines without aura" in (
        reason_section.content
    )
    assert "The provided medical record segments were analysed" not in (
        reason_section.content
    )


def test_keyword_filter_fallback_keeps_clinical_and_plan_content() -> None:
    backend = StubBackend(
        responses={
            1: {
                "overview": "Immunization follow-up visit.",
                "key_points": ["Tdap booster administered."],
                "clinical_details": [
                    "No adverse reactions documented after vaccination."
                ],
                "care_plan": ["Hydration and home rest encouraged over the weekend."],
                "diagnoses": ["Encounter for immunization"],
                "providers": ["Dr. Jane Doe"],
                "medications": ["Tdap booster"],
            }
        }
    )
    summariser = RefactoredSummariser(backend=backend, min_summary_chars=300)
    contract = SummaryContract.from_mapping(
        summariser.summarise("Tdap booster provided.")
    )

    findings_section = next(
        section for section in contract.sections if section.slug == "clinical_findings"
    )
    plan_section = next(
        section
        for section in contract.sections
        if section.slug == "treatment_follow_up_plan"
    )

    assert (
        "No adverse reactions documented after vaccination" in findings_section.content
    )
    assert "Hydration and home rest encouraged over the weekend" in plan_section.content


def test_large_ocr_like_input_retains_content_and_claim_evidence() -> None:
    text, pages = _build_large_ocr_like_payload(page_count=8)
    backend = MarkerAwareBackend()
    summariser = RefactoredSummariser(
        backend=backend,
        target_chars=700,
        max_chars=900,
        overlap_chars=120,
    )
    contract = SummaryContract.from_mapping(
        summariser.summarise(
            text,
            doc_metadata={"facility": "Riverside Clinic", "pages": pages},
        )
    )

    assert backend.calls >= 6
    reason_section = next(
        section for section in contract.sections if section.slug == "reason_for_visit"
    )
    findings_section = next(
        section for section in contract.sections if section.slug == "clinical_findings"
    )
    diagnoses_section = next(
        section for section in contract.sections if section.slug == "diagnoses"
    )
    diagnosis_items = diagnoses_section.extra.get("items", [])

    assert "DX-01" in reason_section.content
    assert (
        "The provided medical record segments were analysed"
        not in reason_section.content
    )
    assert "No specific findings documented in OCR text" not in findings_section.content
    assert diagnosis_items and any("DX-01" in item for item in diagnosis_items)
    assert any("DX-08" in item for item in diagnosis_items)
    assert contract.claims and contract.evidence_spans
    assert contract.claims_notice is None


def test_openai_backend_schema_mismatch_raises(monkeypatch):
    backend = OpenAIResponsesBackend(model="gpt-test", api_key="key")

    def _fake_create(**_: Any):
        return SimpleNamespace(
            output=[
                SimpleNamespace(
                    content=[
                        SimpleNamespace(
                            type="output_text",
                            text=json.dumps(
                                {
                                    "overview": "Example chunk without schema version",
                                    "key_points": [],
                                    "clinical_details": [],
                                    "care_plan": [],
                                    "diagnoses": [],
                                    "providers": [],
                                    "medications": [],
                                }
                            ),
                        )
                    ]
                )
            ]
        )

    class _FakeClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key
            self.responses = SimpleNamespace(create=_fake_create)

    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(OpenAI=lambda api_key: _FakeClient(api_key)),
    )

    with pytest.raises(SummarizationError):
        backend.summarise_chunk(
            chunk_text="data", chunk_index=1, total_chunks=1, estimated_tokens=500
        )


def test_openai_backend_uses_responses_text_format(monkeypatch):
    backend = OpenAIResponsesBackend(model="gpt-test", api_key="key")
    captured: Dict[str, Any] = {}
    payload = {
        "overview": "Structured summary chunk",
        "key_points": ["Key point"],
        "clinical_details": ["Clinical detail"],
        "care_plan": ["Care plan"],
        "diagnoses": ["Diagnosis"],
        "providers": ["Provider"],
        "medications": ["Medication"],
        "schema_version": OpenAIResponsesBackend.CHUNK_SCHEMA_VERSION,
    }

    def _fake_create(**kwargs: Any):
        captured.update(kwargs)
        return SimpleNamespace(
            output=[
                SimpleNamespace(
                    content=[
                        SimpleNamespace(type="output_text", text=json.dumps(payload))
                    ]
                )
            ]
        )

    class _FakeClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key
            self.responses = SimpleNamespace(create=_fake_create)

    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(OpenAI=lambda api_key: _FakeClient(api_key)),
    )

    result = backend.summarise_chunk(
        chunk_text="data", chunk_index=1, total_chunks=1, estimated_tokens=120
    )

    assert "response_format" not in captured
    assert captured["text"]["format"]["type"] == "json_schema"
    assert captured["text"]["format"]["name"] == "chunk_summary_v2025_10_01"
    assert result["schema_version"] == OpenAIResponsesBackend.CHUNK_SCHEMA_VERSION


def test_one_shot_backend_uses_responses_text_format_and_reasoning(monkeypatch):
    backend = OpenAIOneShotResponsesBackend(
        model="gpt-5.4", api_key="key", reasoning_effort="none"
    )
    captured: Dict[str, Any] = {}
    payload = {
        "overview": "Structured summary document",
        "key_points": ["Key point"],
        "clinical_details": ["Clinical detail"],
        "care_plan": ["Care plan"],
        "diagnoses": ["Diagnosis"],
        "providers": ["Provider"],
        "medications": ["Medication"],
        "schema_version": OpenAIOneShotResponsesBackend.DOCUMENT_SCHEMA_VERSION,
    }

    def _fake_create(**kwargs: Any):
        captured.update(kwargs)
        return SimpleNamespace(
            output=[
                SimpleNamespace(
                    content=[
                        SimpleNamespace(type="output_text", text=json.dumps(payload))
                    ]
                )
            ]
        )

    class _FakeClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key
            self.responses = SimpleNamespace(create=_fake_create)

    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(OpenAI=lambda api_key: _FakeClient(api_key)),
    )

    result = backend.summarise_document(
        document_text="Patient seen for chest pain follow-up.",
        estimated_tokens=128,
        page_count=3,
    )

    assert captured["reasoning"] == {"effort": "none"}
    assert captured["text"]["format"]["type"] == "json_schema"
    assert captured["text"]["format"]["name"] == "document_summary_v2025_10_01"
    assert result["schema_version"] == OpenAIOneShotResponsesBackend.DOCUMENT_SCHEMA_VERSION


def test_select_summary_route_prefers_one_shot_for_clean_document():
    text = (
        "Follow-up visit for hypertension management with Dr. Amelia Stone. "
        "Blood pressure improved to 132/84 after consistent medication adherence. "
        "Continue Lisinopril 20 mg daily and return in six weeks for reassessment. "
    ) * 12

    route = _select_summary_route(
        text=text,
        pages=[{"text": text}],
        requested_strategy="auto",
        one_shot_token_threshold=20_000,
        one_shot_max_pages=40,
        noise_ratio_threshold=0.18,
        short_line_ratio_threshold=0.42,
    )

    assert route.selected_strategy == "one_shot"
    assert route.metrics.quality_ok is True
    assert route.reason == "within_operational_threshold_and_quality_budget"


def test_select_summary_route_falls_back_to_chunked_for_noisy_packet():
    text = (
        "Patient Name: Jordan Carter\n"
        "Medical Record Number: SYN-001\n"
        "I understand that the following procedure carries risks and hazards.\n"
        "Call 911 if symptoms worsen.\n"
    ) * 12

    route = _select_summary_route(
        text=text,
        pages=[{"text": text}] * 4,
        requested_strategy="auto",
        one_shot_token_threshold=50_000,
        one_shot_max_pages=40,
        noise_ratio_threshold=0.18,
        short_line_ratio_threshold=0.42,
    )

    assert route.selected_strategy == "chunked"
    assert route.reason in {
        "ocr_quality_requires_chunk_fallback",
        "mixed_packet_signals_detected",
    }


def test_prepare_summary_source_filters_admin_pages_and_duplicate_visit_templates():
    doc_metadata = {
        "pages": [
            {
                "page_number": 1,
                "text": (
                    "MEDICAL RECORDS AFFIDAVIT. STATE OF TEXAS. "
                    "Authorization to disclose protected health information. "
                    "The records custodian states that these attached records are true and correct, "
                    "that the affidavit is made in the regular course of business, and that release "
                    "of information is authorized for legal review."
                ),
            },
            {
                "page_number": 2,
                "text": (
                    "NextCare URGENT CARE. ESTABLISHED PATIENT VISIT. "
                    "This 45 year old male presents for Back pain after lifting boxes. "
                    "Assessment: lumbar strain. Plan: continue ibuprofen 400 mg and follow up in two weeks. "
                    "Lumbar tenderness and reduced range of motion were documented on exam with no neurologic deficit."
                ),
            },
            {
                "page_number": 3,
                "text": (
                    "I understand this care/procedure(s) does not guarantee result or a cure. "
                    "Risk of non-treatment discussed. I have been given an opportunity to ask questions "
                    "about alternative forms of treatment, steps that will occur during my care, and the "
                    "possible complications that may arise."
                ),
            },
            {
                "page_number": 4,
                "text": (
                    "NextCare URGENT CARE. ESTABLISHED PATIENT VISIT. "
                    "This 45 year old male presents for Back pain after lifting boxes. "
                    "Assessment: lumbar strain. Plan: continue ibuprofen 400 mg and follow up in two weeks. "
                    "Lumbar tenderness and reduced range of motion were documented on exam with no neurologic deficit."
                ),
            },
            {
                "page_number": 5,
                "text": (
                    "Lumbar exam showed paraspinal tenderness with reduced range of motion. "
                    "Diagnosis: lumbar strain. Follow-up in two weeks. The patient reports pain after "
                    "lifting boxes at work and was advised to continue stretching, use ibuprofen 400 mg "
                    "as needed, and return promptly if symptoms worsen."
                ),
            },
        ]
    }

    filtered_text, prepared_metadata = _prepare_summary_source("raw text", doc_metadata)

    assert prepared_metadata is not None
    assert prepared_metadata["summary_page_filter"]["applied"] is True
    assert prepared_metadata["summary_page_filter"]["selected_pages"] == 2
    assert [page["page_number"] for page in prepared_metadata["pages"]] == [2, 5]
    assert "AFFIDAVIT" not in filtered_text.upper()
    assert "does not guarantee result or a cure" not in filtered_text
    assert filtered_text.count("ESTABLISHED PATIENT VISIT") == 1
    assert "lumbar exam showed paraspinal tenderness" in filtered_text.lower()


def test_prepare_summary_source_filters_billing_fax_and_portal_noise():
    doc_metadata = {
        "pages": [
            {
                "page_number": 1,
                "text": (
                    "ITEMIZED STATEMENT. Account Information: Anthony Williams. "
                    "Practice Information: PrimaCare Medical Centers. "
                    "To: +12145209941. From: 16075972631. Page: 004 of 100. "
                    "Print Date/Time: 2025-03-14 18:37:16 GMT. "
                    "E&M Code: 99213. CPT 72100. Plan of care for pain medication review."
                ),
            },
            {
                "page_number": 2,
                "text": (
                    "NextCare ANYWHERE. Access Your Visit Summary. Pay Your Bill Online. "
                    "Get X-ray & Lab Results. If your illness or injury does not improve or gets worse, "
                    "we would be happy to re-evaluate you back at a NextCare clinic."
                ),
            },
            {
                "page_number": 3,
                "text": (
                    "To: +12145209941. From: 16075972631. Page: 009 of 100. "
                    "MRI Lumbar Spine without Contrast. Clinical Indication: lumbar pain after a fall at work. "
                    "Findings: L4-5 disc bulge with facet arthropathy and mild foraminal narrowing. "
                    "Impression: lumbar radiculopathy with multilevel degenerative change. "
                    "Provider reviewed the MRI and planned follow-up in two weeks."
                ),
            },
            {
                "page_number": 4,
                "text": (
                    "ESTABLISHED PATIENT VISIT. This 45 year old male presents for low back pain after lifting at work. "
                    "History of Present Illness: Symptoms improve with rest and worsen with bending. "
                    "Assessment: lumbar strain with muscle spasm. "
                    "Plan: continue ibuprofen 400 mg, stretching, and follow up in two weeks."
                ),
            },
        ]
    }

    filtered_text, prepared_metadata = _prepare_summary_source("raw text", doc_metadata)

    assert prepared_metadata is not None
    assert prepared_metadata["summary_page_filter"]["applied"] is True
    assert prepared_metadata["summary_page_filter"]["selected_pages"] == 2
    assert [page["page_number"] for page in prepared_metadata["pages"]] == [3, 4]
    assert "ITEMIZED STATEMENT" not in filtered_text
    assert "Access Your Visit Summary" not in filtered_text
    assert "MRI Lumbar Spine without Contrast" in filtered_text
    assert "ESTABLISHED PATIENT VISIT" in filtered_text


def test_adaptive_summariser_uses_one_shot_for_clean_input():
    chunk_backend = MarkerAwareBackend()
    chunked_summariser = RefactoredSummariser(
        backend=chunk_backend,
        target_chars=700,
        max_chars=900,
        overlap_chars=120,
    )
    one_shot_backend = StubOneShotBackend(
        payload={
            "overview": "Follow-up visit for chronic migraine management.",
            "key_points": ["Patient reports improved headache frequency."],
            "clinical_details": ["Neurological examination remained non-focal."],
            "care_plan": ["Continue sumatriptan 50 mg as needed and follow up in 6 weeks."],
            "diagnoses": ["Chronic migraine without aura"],
            "providers": ["Dr. Alicia Carter"],
            "medications": ["Sumatriptan 50 mg as needed"],
            "schema_version": OpenAIOneShotResponsesBackend.DOCUMENT_SCHEMA_VERSION,
        }
    )
    summariser = AdaptiveSummariser(
        chunked_summariser=chunked_summariser,
        one_shot_backend=one_shot_backend,
        requested_strategy="auto",
        one_shot_token_threshold=20_000,
    )

    result = summariser.summarise_with_details(
        "Follow-up neurology visit for chronic migraine management." * 20,
        doc_metadata={"facility": "MCC Neurology"},
    )
    contract = SummaryContract.from_mapping(result.summary)

    assert result.final_strategy == "one_shot"
    assert one_shot_backend.calls == 1
    assert chunk_backend.calls == 0
    assert result.summary["metadata"]["summary_strategy_used"] == "one_shot"
    assert any(section.slug == "reason_for_visit" for section in contract.sections)


def test_adaptive_summariser_persists_provider_usage_metadata():
    chunk_backend = MarkerAwareBackend()
    chunked_summariser = RefactoredSummariser(backend=chunk_backend)
    one_shot_backend = StubOneShotBackend(
        payload={
            "overview": "Follow-up visit for chronic migraine management.",
            "key_points": ["Patient reports improved headache frequency."],
            "clinical_details": ["Neurological examination remained non-focal."],
            "care_plan": ["Continue sumatriptan 50 mg as needed and follow up in 6 weeks."],
            "diagnoses": ["Chronic migraine without aura"],
            "providers": ["Dr. Alicia Carter"],
            "medications": ["Sumatriptan 50 mg as needed"],
            "schema_version": OpenAIOneShotResponsesBackend.DOCUMENT_SCHEMA_VERSION,
            "_provider_usage": {
                "requests": 1,
                "input_tokens": 321,
                "output_tokens": 89,
                "total_tokens": 410,
                "cached_tokens": 12,
                "reasoning_tokens": 7,
            },
        }
    )
    summariser = AdaptiveSummariser(
        chunked_summariser=chunked_summariser,
        one_shot_backend=one_shot_backend,
        requested_strategy="one_shot",
        one_shot_token_threshold=20_000,
    )

    result = summariser.summarise_with_details(
        "Follow-up neurology visit for chronic migraine management." * 8,
        doc_metadata={"facility": "MCC Neurology"},
    )

    metadata = result.summary["metadata"]
    assert metadata["provider_usage_available"] is True
    assert metadata["provider_usage"] == {
        "requests": 1,
        "input_tokens": 321,
        "output_tokens": 89,
        "total_tokens": 410,
        "cached_tokens": 12,
        "reasoning_tokens": 7,
    }


def test_adaptive_summariser_falls_back_to_chunked_when_one_shot_fails():
    chunk_backend = MarkerAwareBackend()
    chunked_summariser = RefactoredSummariser(
        backend=chunk_backend,
        target_chars=500,
        max_chars=700,
        overlap_chars=80,
    )
    one_shot_backend = StubOneShotBackend(payload={}, error="one-shot parse failure")
    summariser = AdaptiveSummariser(
        chunked_summariser=chunked_summariser,
        one_shot_backend=one_shot_backend,
        requested_strategy="one_shot",
        one_shot_token_threshold=50_000,
    )

    result = summariser.summarise_with_details(
        ("Patient seen by Dr. Provider1 for lumbar pain and DX-01. " * 80),
        doc_metadata={"facility": "Riverside Clinic"},
    )

    assert result.final_strategy == "chunked"
    assert "one-shot parse failure" in (result.fallback_reason or "")
    assert one_shot_backend.calls == 1
    assert chunk_backend.calls >= 1
    assert result.summary["metadata"]["summary_strategy_selected"] == "one_shot"
    assert result.summary["metadata"]["summary_strategy_used"] == "chunked"


def test_refactored_summariser_aggregates_chunk_provider_usage():
    text = (
        "Patient seen by Dr. Provider1 for lumbar pain and DX-01. "
        "Continue Med-01 and physical therapy. "
    ) * 80
    backend = StubBackend(
        responses={
            1: {
                "overview": "Follow-up visit for lumbar pain.",
                "key_points": ["Lumbar pain remains under review."],
                "clinical_details": ["Exam remains consistent with lumbar strain."],
                "care_plan": ["Continue therapy and reassess."],
                "diagnoses": ["Lumbar strain"],
                "providers": ["Dr. Provider1"],
                "medications": ["Med-01"],
                "_provider_usage": {
                    "requests": 1,
                    "input_tokens": 100,
                    "output_tokens": 25,
                    "total_tokens": 125,
                    "cached_tokens": 5,
                    "reasoning_tokens": 2,
                },
            },
            2: {
                "overview": "Ongoing reassessment for lumbar pain.",
                "key_points": ["Symptoms continue but treatment is helping."],
                "clinical_details": ["Range of motion remains reduced."],
                "care_plan": ["Maintain exercises and follow-up."],
                "diagnoses": ["Lumbar strain"],
                "providers": ["Dr. Provider1"],
                "medications": ["Med-01"],
                "_provider_usage": {
                    "requests": 1,
                    "input_tokens": 120,
                    "output_tokens": 30,
                    "total_tokens": 150,
                    "cached_tokens": 0,
                    "reasoning_tokens": 4,
                },
            },
        }
    )
    summariser = RefactoredSummariser(
        backend=backend,
        target_chars=500,
        max_chars=650,
        overlap_chars=80,
    )

    summary = summariser.summarise(text)
    usage = summary["metadata"]["provider_usage"]

    assert summary["metadata"]["provider_usage_available"] is True
    assert usage["requests"] >= 2
    assert usage["input_tokens"] >= 220
    assert usage["output_tokens"] >= 55
    assert usage["total_tokens"] >= 275
    assert usage["reasoning_tokens"] >= 6


def test_build_contract_filters_fixture_style_provider_and_medication_contamination():
    summariser = RefactoredSummariser(backend=StubBackend(responses={1: {}}))
    payload = {
        "overview": "Follow-up visit for persistent low back pain after a workplace fall.",
        "key_points": ["Back pain persisted after the workplace injury."],
        "clinical_details": [
            "Lumbar tenderness and reduced range of motion were documented on exam."
        ],
        "care_plan": ["Continue ibuprofen 800mg and cyclobenzaprine 10mg as needed."],
        "diagnoses": ["Lumbar strain"],
        "providers": [
            "Dr. If not met, D.O.",
            "I will not operate a motor vehicle or equipment when I use my medications unless expressly approved by my provider at Greater Texas Orthopedic Associates, PLLC",
            "Anolynn Loudermilk, FNP",
        ],
        "medications": [
            "It is important to always keep an active list of medications available so that you can share with other providers and manage your medications appropriately",
            "PLEASE FILL YOUR PRESCRIPTIONS AND TAKE ALL MEDICATIONS AS DIRECTED/PRESCRIBED EVEN IF YOU BEGIN TO FEEL BETTER BEFORE YOU RUN OUT OF MEDICINE",
            "Ibuprofen 800mg",
            "Cyclobenzaprine 10mg",
        ],
    }
    raw_text = (
        "If not met, D.O.\n"
        "Electronically Signed by: Michael Davis, MD\n"
        "Provider: Anolynn Loudermilk, FNP\n"
        "MEDICATIONS: Ibuprofen 800mg and Cyclobenzaprine 10mg\n"
    )

    summary = summariser.build_contract_from_payload(
        payload,
        raw_text=raw_text,
        doc_metadata={"facility": "NextCare"},
    )
    contract = SummaryContract.from_mapping(summary)
    provider_section = next(
        section for section in contract.sections if section.slug == "provider_seen"
    )
    providers_section = next(
        section
        for section in contract.sections
        if section.slug == "healthcare_providers"
    )
    medications_section = next(
        section for section in contract.sections if section.slug == "medications"
    )
    provider_items = provider_section.extra.get("items", [])
    roster_items = providers_section.extra.get("items", [])
    medication_items = medications_section.extra.get("items", [])

    assert "If not met" not in provider_section.content
    assert not any("If not met" in item for item in provider_items)
    assert any("Anolynn Loudermilk" in item for item in roster_items)
    assert medication_items == ["Ibuprofen 800mg", "Cyclobenzaprine 10mg"]


def test_heuristic_backend_includes_schema_version():
    backend = HeuristicChunkBackend()
    result = backend.summarise_chunk(
        chunk_text="Patient seen for hypertension follow-up. Blood pressure well controlled.",
        chunk_index=1,
        total_chunks=1,
        estimated_tokens=120,
    )
    assert result["schema_version"] == OpenAIResponsesBackend.CHUNK_SCHEMA_VERSION


def test_heuristic_backend_entity_extraction():
    backend = HeuristicChunkBackend()
    text = (
        "Dr. John Smith reviewed the patient's hypertension and diabetes treatment plan. "
        "Lisinopril 20 mg tablet prescribed daily with Hydrotherapy diet adjustments. "
        "Follow-up recommended in six weeks to monitor medication efficacy."
    )
    result = backend.summarise_chunk(
        chunk_text=text,
        chunk_index=1,
        total_chunks=1,
        estimated_tokens=200,
    )
    assert any("John Smith" in provider for provider in result["providers"])
    assert any("Lisinopril 20 mg" in med for med in result["medications"])
    assert result["care_plan"], "Care plan should be populated from follow-up sentence"


def test_heuristic_backend_filters_demographics_from_reason_and_plan_sections():
    backend = HeuristicChunkBackend()
    text = (
        "Patient Name: Jordan Carter\n\n"
        "Date of Visit: 2026-03-07\n\n"
        "Medical Record Number: SYN-20260307\n\n"
        "Reason for Visit: Follow-up visit for low back pain after lifting storage boxes at work. "
        "Jordan Carter reports low back pain, lumbar strain, stiffness, and intermittent muscle spasm.\n\n"
        "Plan: Continue ibuprofen 400 mg as needed, cyclobenzaprine 5 mg at bedtime, heating pad, "
        "daily stretching exercises, and modified duty for one week.\n\n"
        "Follow-up: Return in two weeks for reassessment of low back pain and lumbar strain."
    )

    result = backend.summarise_chunk(
        chunk_text=text,
        chunk_index=1,
        total_chunks=1,
        estimated_tokens=180,
    )

    assert result["overview"].startswith("Reason for Visit:")
    assert not any("Patient Name:" in item for item in result["key_points"])
    assert not any("Date of Visit:" in item for item in result["key_points"])
    assert not any("Medical Record Number:" in item for item in result["key_points"])
    assert not any("Patient Name:" in item for item in result["care_plan"])
    assert result["care_plan"] == [
        "Plan: Continue ibuprofen 400 mg as needed, cyclobenzaprine 5 mg at bedtime, heating pad, daily stretching exercises, and modified duty for one week",
        "Follow-up: Return in two weeks for reassessment of low back pain and lumbar strain",
    ]


def test_refactored_summary_contract_excludes_demographic_header_leakage():
    text = (
        "Patient Name: Jordan Carter\n\n"
        "Date of Visit: 2026-03-07\n\n"
        "Medical Record Number: SYN-20260307\n\n"
        "Reason for Visit: Follow-up visit for low back pain after lifting storage boxes at work. "
        "Jordan Carter reports low back pain, lumbar strain, stiffness, and intermittent muscle spasm. "
        "Symptoms improve with rest, gentle stretching, and heat, and worsen after bending, lifting, "
        "or prolonged standing.\n\n"
        "History of Present Illness: The patient is improving but still has morning stiffness and pain "
        "with repeated lifting. Symptoms remain localized to the low back without leg weakness, bowel "
        "changes, or bladder changes.\n\n"
        "Plan: Continue ibuprofen 400 mg as needed, cyclobenzaprine 5 mg at bedtime, heating pad, "
        "daily stretching exercises, and modified duty for one week.\n\n"
        "Follow-up: Return in two weeks for reassessment of low back pain and lumbar strain."
    )

    summary = RefactoredSummariser(backend=HeuristicChunkBackend()).summarise(text)
    sections = {section["slug"]: section["content"] for section in summary["sections"]}

    assert "Patient Name:" not in sections["reason_for_visit"]
    assert "Date of Visit:" not in sections["reason_for_visit"]
    assert "Medical Record Number:" not in sections["reason_for_visit"]
    assert "Patient Name:" not in sections["treatment_follow_up_plan"]
    assert "Date of Visit:" not in sections["treatment_follow_up_plan"]
    assert "Medical Record Number:" not in sections["treatment_follow_up_plan"]
    assert "Follow-up visit for low back pain after lifting storage boxes at work" in sections["reason_for_visit"]
    assert "Continue ibuprofen 400 mg as needed" in sections["treatment_follow_up_plan"]


def test_refactored_summary_includes_hpi_reason_line_for_live_style_ocr_fixture():
    text = (
        "Patient Name: Jordan Carter\n\n"
        "Date of Visit: 2026-03-07\n\n"
        "Medical Record Number: SYN-20260307\n\n"
        "Reason for Visit: Follow-up visit for low back pain after lifting storage boxes at work. "
        "Jordan Carter reports low back pain, lumbar strain, stiffness, and intermittent muscle spasm. "
        "Symptoms improve with rest, gentle stretching, and heat, and worsen after bending, lifting, or prolonged standing.\n\n"
        "History of Present Illness: The patient is improving but still has morning stiffness and pain with repeated lifting. "
        "Symptoms remain localized to the low back without leg weakness, bowel changes, or bladder changes.\n\n"
        "Examination: Lumbar paraspinal tenderness, reduced lumbar range of motion, mild muscle spasm, normal gait, "
        "normal strength, intact sensation, and negative straight leg raise bilaterally.\n\n"
        "Assessment: Low back pain with lumbar strain and associated muscle spasm after repetitive lifting. "
        "Symptoms are improving with conservative treatment and no red flag neurologic findings are present.\n\n"
        "Diagnoses: Low back pain; lumbar strain; muscle spasm.\n\n"
        "Medications: Ibuprofen 400 mg as needed and cyclobenzaprine 5 mg at bedtime.\n\n"
        "Plan: Continue ibuprofen 400 mg as needed, cyclobenzaprine 5 mg at bedtime, heating pad, daily stretching "
        "exercises, and modified duty for one week.\n\n"
        "Follow-up: Return in two weeks for reassessment of low back pain and lumbar strain."
    )

    summary = RefactoredSummariser(backend=HeuristicChunkBackend()).summarise(text)
    sections = {section["slug"]: section["content"] for section in summary["sections"]}

    assert (
        "History of Present Illness: The patient is improving but still has morning stiffness and pain with repeated lifting."
        in sections["reason_for_visit"]
    )


def test_split_gcs_uri_validation():
    bucket, blob = _split_gcs_uri("gs://bucket/path/file.json")
    assert bucket == "bucket"
    assert blob == "path/file.json"
    with pytest.raises(SummarizationError):
        _split_gcs_uri("http://invalid/path")
    with pytest.raises(SummarizationError):
        _split_gcs_uri("gs://bucket")


def test_merge_dicts_overrides_keys():
    base = {"a": 1, "b": 2}
    patch = {"b": 3, "c": 4}
    merged = _merge_dicts(base, patch)
    assert merged == {"a": 1, "b": 3, "c": 4}
    assert base["b"] == 2  # original not mutated


def test_load_input_payload_variants(tmp_path):
    data = {
        "text": "Example text body",
        "pages": [{"text": "Example page 1"}],
        "metadata": {"source": "unit-test"},
    }
    payload_path = tmp_path / "input.json"
    payload_path.write_text(json.dumps(data), encoding="utf-8")
    text, metadata, pages = _load_input_payload(payload_path)
    assert text.startswith("Example")
    assert metadata["source"] == "unit-test"
    assert pages and pages[0]["text"] == "Example page 1"

    payload_path.write_text(
        json.dumps({"pages": [{"text": "Only pages"}]}), encoding="utf-8"
    )
    rewritten_text, _, rewritten_pages = _load_input_payload(payload_path)
    assert rewritten_text.strip() == "Only pages"
    assert rewritten_pages and rewritten_pages[0]["text"] == "Only pages"

    payload_path.write_text(json.dumps({"invalid": "structure"}), encoding="utf-8")
    with pytest.raises(SummarizationError):
        _load_input_payload(payload_path)


def test_load_input_payload_rebuilds_paragraphs_from_layout(tmp_path):
    raw_text = (
        "MCC OCR Smoke Test - NON-PHI Fictional Clinical Follow-Up\n"
        "Generated UTC: 2026-03-07T20-04-35Z\n"
        "Reason for Visit: Follow-up visit for low back pain after lifting storage boxes at work.\n"
        "Assessment: Symptoms are improving with conservative treatment.\n"
        "Plan: Continue ibuprofen 400 mg as needed and follow up in two weeks.\n"
    )

    def _segment(fragment: str) -> dict[str, dict[str, list[dict[str, int]]]]:
        start = raw_text.index(fragment)
        end = start + len(fragment)
        return {
            "layout": {
                "textAnchor": {
                    "textSegments": [{"startIndex": start, "endIndex": end}]
                }
            }
        }

    payload = {
        "document": {
            "text": raw_text,
            "pages": [
                {
                    "paragraphs": [
                        _segment("MCC OCR Smoke Test - NON-PHI Fictional Clinical Follow-Up"),
                        _segment("Generated UTC: 2026-03-07T20-04-35Z"),
                        _segment(
                            "Reason for Visit: Follow-up visit for low back pain after lifting storage boxes at work."
                        ),
                        _segment(
                            "Assessment: Symptoms are improving with conservative treatment."
                        ),
                        _segment(
                            "Plan: Continue ibuprofen 400 mg as needed and follow up in two weeks."
                        ),
                    ]
                }
            ],
        }
    }
    payload_path = tmp_path / "layout-input.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    text, metadata, pages = _load_input_payload(payload_path)

    assert metadata["pages"] == payload["document"]["pages"]
    assert len(pages) == 1
    assert _count_text_paragraphs(text) == 5
    assert "\n\nReason for Visit:" in text
    assert "Plan: Continue ibuprofen 400 mg as needed" in text


def test_load_input_payload_gcs_blob(monkeypatch):
    payload = {
        "text": "Remote text body",
        "pages": [{"text": "Remote text body"}],
        "metadata": {"source": "gcs"},
    }
    blob_bytes = json.dumps(payload).encode("utf-8")

    class _StubBlob:
        def __init__(self, name: str, data: bytes | None) -> None:
            self.name = name
            self._data = data

        def download_as_bytes(self) -> bytes:
            if self._data is None:
                raise FileNotFoundError("missing blob")
            return self._data

    class _StubBucket:
        def __init__(self, blobs: Dict[str, bytes | None]) -> None:
            self._blobs = blobs

        def blob(self, name: str) -> _StubBlob:
            return _StubBlob(name, self._blobs.get(name))

    class _StubClient:
        def __init__(self, blobs: Dict[str, bytes | None]) -> None:
            self._blobs = blobs

        def bucket(self, _: str) -> _StubBucket:
            return _StubBucket(self._blobs)

        def list_blobs(
            self, *_args, **_kwargs
        ):  # pragma: no cover - not used in this scenario
            return []

    monkeypatch.setattr(
        "google.cloud.storage.Client",
        lambda: _StubClient({"ocr/job/aggregate.json": blob_bytes}),
    )

    text, metadata, pages = _load_input_payload("gs://bucket/ocr/job/aggregate.json")
    assert text == "Remote text body"
    assert metadata["source"] == "gcs"
    assert pages and pages[0]["text"] == "Remote text body"


def test_load_input_payload_gcs_prefix_fallback(monkeypatch):
    raw_text = (
        "Patient Name: Jordan Carter\n"
        "Date of Visit: 2026-03-07\n"
        "Medical Record Number: SYN-20260307\n"
        "Reason for Visit: Follow-up visit for low back pain after lifting storage boxes at work.\n"
        "Plan: Continue ibuprofen 400 mg as needed.\n"
    )
    visit_start = raw_text.index("Reason for Visit:")
    plan_start = raw_text.index("Plan:")
    shard_payload = {
        "document": {
            "text": raw_text,
            "pages": [
                {
                    "paragraphs": [
                        {
                            "layout": {
                                "textAnchor": {
                                    "textSegments": [
                                        {
                                            "startIndex": 0,
                                            "endIndex": visit_start,
                                        }
                                    ]
                                }
                            }
                        },
                        {
                            "layout": {
                                "textAnchor": {
                                    "textSegments": [
                                        {
                                            "startIndex": visit_start,
                                            "endIndex": plan_start,
                                        }
                                    ]
                                }
                            }
                        },
                        {
                            "layout": {
                                "textAnchor": {
                                    "textSegments": [
                                        {
                                            "startIndex": plan_start,
                                            "endIndex": len(raw_text),
                                        }
                                    ]
                                }
                            }
                        },
                    ]
                }
            ],
            "metadata": {"shard": "0"},
        }
    }
    shard_bytes = json.dumps(shard_payload).encode("utf-8")

    class _StubBlob:
        def __init__(self, name: str, data: bytes | None) -> None:
            self.name = name
            self._data = data

        def download_as_bytes(self) -> bytes:
            if self._data is None:
                raise FileNotFoundError("missing blob")
            return self._data

    class _StubBucket:
        def __init__(self, blobs: Dict[str, bytes | None]) -> None:
            self._blobs = blobs

        def blob(self, name: str) -> _StubBlob:
            return _StubBlob(name, self._blobs.get(name))

    class _StubClient:
        def __init__(self, blobs: Dict[str, bytes | None]) -> None:
            self._blobs = blobs

        def bucket(self, _: str) -> _StubBucket:
            return _StubBucket(self._blobs)

        def list_blobs(self, _bucket: str, prefix: str):
            return [
                _StubBlob(name, data)
                for name, data in self._blobs.items()
                if name.startswith(prefix)
            ]

    blobs = {
        "ocr/job/aggregate.json": None,
        "ocr/job/0/shard.json": shard_bytes,
    }
    monkeypatch.setattr("google.cloud.storage.Client", lambda: _StubClient(blobs))

    text, metadata, pages = _load_input_payload("gs://bucket/ocr/job/aggregate.json")
    assert "Follow-up visit for low back pain after lifting storage boxes at work." in text
    assert "Continue ibuprofen 400 mg as needed." in text
    assert "SYN-20260307" in text
    assert "stora ge" not in text
    assert pages and pages[0]["paragraphs"]
    assert metadata.get("shard") == "0"


def test_gcs_prefix_fallback_preserves_pages_for_async_route_selection(monkeypatch):
    admin_page = (
        "AFFIDAVIT OF CUSTODIAN OF RECORDS. I understand this care/procedure does not "
        "guarantee result or a cure. Call 911 or go to the emergency room if symptoms worsen."
    )
    clinical_page = (
        "NextCare URGENT CARE. ESTABLISHED PATIENT VISIT. Patient presents for back pain "
        "after lifting boxes at work. Assessment: lumbar strain with paraspinal tenderness "
        "and reduced range of motion. Plan: continue ibuprofen 400 mg as needed and follow "
        "up in two weeks."
    )
    shard_payload = {
        "document": {
            "text": f"{admin_page}\n\n{clinical_page}",
            "pages": [
                {"page_number": 1, "text": admin_page},
                {"page_number": 2, "text": clinical_page},
            ],
            "metadata": {"shard": "0"},
        }
    }
    shard_bytes = json.dumps(shard_payload).encode("utf-8")

    class _StubBlob:
        def __init__(self, name: str, data: bytes | None) -> None:
            self.name = name
            self._data = data

        def download_as_bytes(self) -> bytes:
            if self._data is None:
                raise FileNotFoundError("missing blob")
            return self._data

    class _StubBucket:
        def __init__(self, blobs: Dict[str, bytes | None]) -> None:
            self._blobs = blobs

        def blob(self, name: str) -> _StubBlob:
            return _StubBlob(name, self._blobs.get(name))

    class _StubClient:
        def __init__(self, blobs: Dict[str, bytes | None]) -> None:
            self._blobs = blobs

        def bucket(self, _: str) -> _StubBucket:
            return _StubBucket(self._blobs)

        def list_blobs(self, _bucket: str, prefix: str):
            return [
                _StubBlob(name, data)
                for name, data in self._blobs.items()
                if name.startswith(prefix)
            ]

    blobs = {
        "ocr/job/aggregate.json": None,
        "ocr/job/0/shard.json": shard_bytes,
    }
    monkeypatch.setattr("google.cloud.storage.Client", lambda: _StubClient(blobs))

    text, metadata, pages = _load_input_payload("gs://bucket/ocr/job/aggregate.json")
    summariser = AdaptiveSummariser(
        chunked_summariser=RefactoredSummariser(backend=HeuristicChunkBackend()),
        one_shot_backend=StubOneShotBackend(
            payload={
                "overview": "Urgent care follow-up for lumbar strain after lifting boxes.",
                "key_points": [
                    "Back pain persisted after a lifting injury at work."
                ],
                "clinical_details": [
                    "Exam documented paraspinal tenderness with reduced lumbar range of motion."
                ],
                "care_plan": [
                    "Continue ibuprofen 400 mg as needed and follow up in two weeks."
                ],
                "diagnoses": ["Lumbar strain"],
                "providers": ["NextCare urgent care clinician"],
                "medications": ["Ibuprofen 400 mg as needed"],
                "schema_version": OpenAIOneShotResponsesBackend.DOCUMENT_SCHEMA_VERSION,
            }
        ),
        requested_strategy="auto",
        one_shot_token_threshold=20_000,
    )

    result = summariser.summarise_with_details(text, doc_metadata=metadata)

    assert len(pages) == 2
    assert isinstance(metadata.get("pages"), list)
    assert result.route.metrics.page_count == 1
    assert result.summary["metadata"]["summary_input_filter_applied"] is True
    assert result.summary["metadata"]["summary_input_pages_total"] == 2
    assert result.summary["metadata"]["summary_input_pages_selected"] == 1


def test_gcs_prefix_fallback_combines_pages_across_shards_for_route_selection(
    monkeypatch,
):
    shard_payloads = {
        "ocr/job/aggregate.json": None,
        "ocr/job/0/shard-0.json": json.dumps(
            {
                "document": {
                    "text": (
                        "Clinical follow-up for lumbar strain with persistent pain.\n\n"
                        "Exam documented lumbar tenderness and reduced range of motion."
                    ),
                    "pages": [
                        {
                            "page_number": 1,
                            "text": (
                                "Clinical follow-up for lumbar strain with persistent pain."
                            ),
                        },
                        {
                            "page_number": 2,
                            "text": (
                                "Exam documented lumbar tenderness and reduced range of motion."
                            ),
                        },
                    ],
                    "metadata": {"shard": "0"},
                }
            }
        ).encode("utf-8"),
        "ocr/job/0/shard-1.json": json.dumps(
            {
                "document": {
                    "text": (
                        "Plan: Continue ibuprofen 400 mg as needed and follow up in two weeks."
                    ),
                    "pages": [
                        {
                            "page_number": 3,
                            "text": (
                                "Plan: Continue ibuprofen 400 mg as needed and follow up in two weeks."
                            ),
                        }
                    ],
                    "metadata": {"shard": "1"},
                }
            }
        ).encode("utf-8"),
    }

    class _StubBlob:
        def __init__(self, name: str, data: bytes | None) -> None:
            self.name = name
            self._data = data

        def download_as_bytes(self) -> bytes:
            if self._data is None:
                raise FileNotFoundError("missing blob")
            return self._data

    class _StubBucket:
        def __init__(self, blobs: Dict[str, bytes | None]) -> None:
            self._blobs = blobs

        def blob(self, name: str) -> _StubBlob:
            return _StubBlob(name, self._blobs.get(name))

    class _StubClient:
        def __init__(self, blobs: Dict[str, bytes | None]) -> None:
            self._blobs = blobs

        def bucket(self, _: str) -> _StubBucket:
            return _StubBucket(self._blobs)

        def list_blobs(self, _bucket: str, prefix: str):
            return [
                _StubBlob(name, data)
                for name, data in sorted(self._blobs.items())
                if name.startswith(prefix)
            ]

    monkeypatch.setattr(
        "google.cloud.storage.Client", lambda: _StubClient(shard_payloads)
    )

    text, metadata, pages = _load_input_payload("gs://bucket/ocr/job/aggregate.json")
    summariser = AdaptiveSummariser(
        chunked_summariser=RefactoredSummariser(backend=HeuristicChunkBackend()),
        one_shot_backend=StubOneShotBackend(
            payload={
                "overview": "Follow-up visit for lumbar strain after a lifting injury.",
                "key_points": [
                    "Exam documented lumbar tenderness with reduced range of motion."
                ],
                "clinical_details": [
                    "Plan was conservative management with ibuprofen and follow-up."
                ],
                "care_plan": [
                    "Continue ibuprofen 400 mg as needed and return in two weeks."
                ],
                "diagnoses": ["Lumbar strain"],
                "providers": ["Treating clinician not documented"],
                "medications": ["Ibuprofen 400 mg as needed"],
                "schema_version": OpenAIOneShotResponsesBackend.DOCUMENT_SCHEMA_VERSION,
            }
        ),
        requested_strategy="auto",
        one_shot_token_threshold=20_000,
    )

    result = summariser.summarise_with_details(text, doc_metadata=metadata)

    assert len(pages) == 3
    assert isinstance(metadata.get("pages"), list)
    assert len(metadata["pages"]) == 3
    assert result.route.metrics.page_count == 3


def test_openai_backend_falls_back_to_heuristic(monkeypatch):
    backend = OpenAIResponsesBackend(model="gpt-test", api_key="test-key")

    class _StubClient:
        class responses:
            @staticmethod
            def create(*_args, **_kwargs):
                raise TypeError("unexpected keyword argument 'response_format'")

    monkeypatch.setitem(
        sys.modules, "openai", SimpleNamespace(OpenAI=lambda api_key: _StubClient())
    )

    result = backend.summarise_chunk(
        chunk_text="Patient reviewed for hypertension follow-up. Blood pressure improved with medication.",
        chunk_index=1,
        total_chunks=1,
        estimated_tokens=120,
    )
    assert result["schema_version"] == OpenAIResponsesBackend.CHUNK_SCHEMA_VERSION
    assert "hypertension" in " ".join(result["diagnoses"]).lower()


def test_openai_backend_falls_back_to_heuristic_on_invalid_json(monkeypatch):
    backend = OpenAIResponsesBackend(model="gpt-test", api_key="test-key")

    class _StubClient:
        class responses:
            @staticmethod
            def create(*_args, **_kwargs):
                return SimpleNamespace(
                    output=[
                        SimpleNamespace(
                            content=[SimpleNamespace(type="output_text", text='{"oops"')]
                        )
                    ]
                )

    monkeypatch.setitem(
        sys.modules, "openai", SimpleNamespace(OpenAI=lambda api_key: _StubClient())
    )

    result = backend.summarise_chunk(
        chunk_text="Patient reviewed for hypertension follow-up. Blood pressure improved with medication.",
        chunk_index=79,
        total_chunks=180,
        estimated_tokens=120,
    )

    assert result["schema_version"] == OpenAIResponsesBackend.CHUNK_SCHEMA_VERSION
    assert "hypertension" in " ".join(result["diagnoses"]).lower()
    assert getattr(backend, "_fallback_used", False) is True


def test_openai_backend_adds_extractive_backup_items(monkeypatch):
    backend = OpenAIResponsesBackend(model="gpt-test", api_key="test-key")
    chunk_text = (
        "Patient Name: Jordan Carter\n\n"
        "Date of Visit: 2026-03-07\n\n"
        "Medical Record Number: SYN-20260307\n\n"
        "Reason for Visit: Follow-up visit for low back pain after lifting storage boxes at work. "
        "Jordan Carter reports low back pain, lumbar strain, stiffness, and intermittent muscle spasm.\n\n"
        "History of Present Illness: The patient is improving but still has morning stiffness and pain with repeated lifting. "
        "Symptoms remain localized to the low back without leg weakness, bowel changes, or bladder changes.\n\n"
        "Examination: Lumbar paraspinal tenderness, reduced lumbar range of motion, mild muscle spasm, normal gait, normal strength, intact sensation, and negative straight leg raise bilaterally.\n\n"
        "Assessment: Findings are consistent with lumbar strain and mechanical low back pain. Symptoms are improving with conservative treatment and no red flag neurologic findings are present.\n\n"
        "Diagnoses: Lumbar strain. Low back pain. Muscle spasm.\n\n"
        "Medications: Ibuprofen 400 mg as needed and cyclobenzaprine 5 mg at bedtime.\n\n"
        "Plan: Continue ibuprofen 400 mg as needed, cyclobenzaprine 5 mg at bedtime, heating pad, daily stretching exercises, and modified duty for one week.\n\n"
        "Follow-up: Return in two weeks for reassessment of low back pain and lumbar strain."
    )
    paraphrased_payload = {
        "overview": "The patient returned for reassessment of persistent lumbar discomfort after a workplace lifting injury.",
        "key_points": [
            "Persistent lumbar discomfort is improving with conservative treatment.",
            "No neurologic red flags were identified during follow-up.",
        ],
        "clinical_details": [
            "Back stiffness and intermittent spasm continue but are improving with rest and stretching.",
            "Examination showed reduced lumbar movement with otherwise stable neurologic findings.",
        ],
        "care_plan": [
            "Continue anti-inflammatory medication, bedtime muscle relaxant, heat, stretching, and temporary work restrictions.",
            "Return in two weeks for reassessment.",
        ],
        "diagnoses": ["Lumbar strain", "Mechanical low back pain"],
        "providers": [],
        "medications": ["Ibuprofen", "Cyclobenzaprine"],
        "schema_version": OpenAIResponsesBackend.CHUNK_SCHEMA_VERSION,
    }

    def _fake_create(**_: Any):
        return SimpleNamespace(
            output=[
                SimpleNamespace(
                    content=[
                        SimpleNamespace(
                            type="output_text",
                            text=json.dumps(paraphrased_payload),
                        )
                    ]
                )
            ]
        )

    class _FakeClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key
            self.responses = SimpleNamespace(create=_fake_create)

    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(OpenAI=lambda api_key: _FakeClient(api_key)),
    )

    result = backend.summarise_chunk(
        chunk_text=chunk_text,
        chunk_index=1,
        total_chunks=1,
        estimated_tokens=340,
    )

    assert result["overview"].startswith(
        "Reason for Visit: Follow-up visit for low back pain after lifting storage boxes at work."
    )
    assert any(
        "follow-up visit for low back pain after lifting storage boxes at work"
        in item.lower()
        for item in result["key_points"]
    )
    assert not any(
        "persistent lumbar discomfort" in item.lower()
        for item in result["key_points"]
    )
    assert any("ibuprofen 400 mg" in item.lower() for item in result["medications"])


def test_openai_backend_grounded_payload_passes_supervisor(monkeypatch):
    backend = OpenAIResponsesBackend(model="gpt-test", api_key="test-key")
    chunk_text = (
        "Patient Name: Jordan Carter\n\n"
        "Date of Visit: 2026-03-07\n\n"
        "Medical Record Number: SYN-20260307\n\n"
        "Reason for Visit: Follow-up visit for low back pain after lifting storage boxes at work. "
        "Jordan Carter reports low back pain, lumbar strain, stiffness, and intermittent muscle spasm.\n\n"
        "History of Present Illness: The patient is improving but still has morning stiffness and pain with repeated lifting. "
        "Symptoms remain localized to the low back without leg weakness, bowel changes, or bladder changes.\n\n"
        "Examination: Lumbar paraspinal tenderness, reduced lumbar range of motion, mild muscle spasm, normal gait, normal strength, intact sensation, and negative straight leg raise bilaterally.\n\n"
        "Assessment: Findings are consistent with lumbar strain and mechanical low back pain. Symptoms are improving with conservative treatment and no red flag neurologic findings are present.\n\n"
        "Diagnoses: Lumbar strain. Low back pain. Muscle spasm.\n\n"
        "Medications: Ibuprofen 400 mg as needed and cyclobenzaprine 5 mg at bedtime.\n\n"
        "Plan: Continue ibuprofen 400 mg as needed, cyclobenzaprine 5 mg at bedtime, heating pad, daily stretching exercises, and modified duty for one week.\n\n"
        "Follow-up: Return in two weeks for reassessment of low back pain and lumbar strain."
    )
    paraphrased_payload = {
        "overview": "The patient returned for reassessment of persistent lumbar discomfort after a workplace lifting injury.",
        "key_points": [
            "Persistent lumbar discomfort is improving with conservative treatment.",
            "No neurologic red flags were identified during follow-up.",
        ],
        "clinical_details": [
            "Back stiffness and intermittent spasm continue but are improving with rest and stretching.",
            "Examination showed reduced lumbar movement with otherwise stable neurologic findings.",
        ],
        "care_plan": [
            "Continue anti-inflammatory medication, bedtime muscle relaxant, heat, stretching, and temporary work restrictions.",
            "Return in two weeks for reassessment.",
        ],
        "diagnoses": ["Lumbar strain", "Mechanical low back pain"],
        "providers": [],
        "medications": ["Ibuprofen", "Cyclobenzaprine"],
        "schema_version": OpenAIResponsesBackend.CHUNK_SCHEMA_VERSION,
    }

    def _fake_create(**_: Any):
        return SimpleNamespace(
            output=[
                SimpleNamespace(
                    content=[
                        SimpleNamespace(
                            type="output_text",
                            text=json.dumps(paraphrased_payload),
                        )
                    ]
                )
            ]
        )

    class _FakeClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key
            self.responses = SimpleNamespace(create=_fake_create)

    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(OpenAI=lambda api_key: _FakeClient(api_key)),
    )

    grounded_payload = backend.summarise_chunk(
        chunk_text=chunk_text,
        chunk_index=1,
        total_chunks=1,
        estimated_tokens=340,
    )
    summary = RefactoredSummariser(
        backend=StubBackend(responses={1: grounded_payload})
    ).summarise(chunk_text)

    supervisor = CommonSenseSupervisor(simple=False)
    doc_stats = supervisor.collect_doc_stats(
        text=chunk_text,
        pages=[{"page_number": 1, "text": chunk_text}],
        file_bytes=None,
    )
    validation = supervisor.validate(
        ocr_text=chunk_text,
        summary=summary,
        doc_stats=doc_stats,
    )

    assert validation["supervisor_passed"]
    assert validation["content_alignment"] >= 0.8


def test_one_shot_backend_extracts_provider_usage(monkeypatch):
    backend = OpenAIOneShotResponsesBackend(model="gpt-test", api_key="test-key")
    payload = {
        "overview": "Follow-up visit for chronic migraine management.",
        "key_points": ["Patient reports improved headache frequency."],
        "clinical_details": ["Neurological examination remained non-focal."],
        "care_plan": ["Continue sumatriptan 50 mg as needed and follow up in 6 weeks."],
        "diagnoses": ["Chronic migraine without aura"],
        "providers": ["Dr. Alicia Carter"],
        "medications": ["Sumatriptan 50 mg as needed"],
        "schema_version": OpenAIOneShotResponsesBackend.DOCUMENT_SCHEMA_VERSION,
    }

    def _fake_create(**_: Any):
        return SimpleNamespace(
            output=[
                SimpleNamespace(
                    content=[
                        SimpleNamespace(
                            type="output_text",
                            text=json.dumps(payload),
                        )
                    ]
                )
            ],
            usage=SimpleNamespace(
                input_tokens=321,
                output_tokens=89,
                total_tokens=410,
                input_tokens_details=SimpleNamespace(cached_tokens=12),
                output_tokens_details=SimpleNamespace(reasoning_tokens=7),
            ),
        )

    class _FakeClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key
            self.responses = SimpleNamespace(create=_fake_create)

    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(OpenAI=lambda api_key: _FakeClient(api_key)),
    )

    result = backend.summarise_document(
        document_text="Patient seen for migraine follow-up.",
        estimated_tokens=128,
        page_count=3,
    )

    assert result["_provider_usage"] == {
        "requests": 1,
        "input_tokens": 321,
        "output_tokens": 89,
        "total_tokens": 410,
        "cached_tokens": 12,
        "reasoning_tokens": 7,
    }


def test_refactored_summary_omits_chunk_count_runtime_marker() -> None:
    text = (
        "Patient reports recurring lumbar pain after an occupational injury. "
        "Dr. Alicia Carter reviewed imaging and confirmed persistent muscle spasm. "
        "Therapy continuation and medication adjustment discussed."
    ) * 8

    backend = StubBackend(
        responses={
            1: {
                "overview": "Follow-up evaluation for persistent lumbar pain.",
                "key_points": [
                    "Symptoms persist with activity-related exacerbation.",
                    "Provider reviewed prior imaging and exam findings.",
                ],
                "clinical_details": [
                    "Lumbar tenderness and reduced flexion documented during examination.",
                    "Neurologic deficits not observed.",
                ],
                "care_plan": [
                    "Continue physical therapy and monitor symptom progression.",
                    "Adjust medication regimen for pain control.",
                ],
                "diagnoses": ["Lumbar strain"],
                "providers": ["Dr. Alicia Carter"],
                "medications": ["Cyclobenzaprine 5 mg"],
            }
        }
    )

    summariser = RefactoredSummariser(
        backend=backend, target_chars=350, max_chars=500, overlap_chars=80
    )
    summary = summariser.summarise(text)
    summary_text = SummaryContract.from_mapping(summary).as_text()
    assert "Document processed in " not in summary_text


def test_cli_falls_back_to_chunked_when_one_shot_backend_fails(monkeypatch, tmp_path):
    input_path = tmp_path / "payload.json"
    input_payload = {
        "text": "Patient seen for follow-up. Blood pressure improving with medication adherence.",
        "metadata": {"job_id": "cli-test"},
    }
    input_path.write_text(json.dumps(input_payload), encoding="utf-8")
    output_path = tmp_path / "summary.json"

    original_summarise = RefactoredSummariser.summarise

    def _fake_summarise(self, text: str, *, doc_metadata: Dict[str, Any] | None = None):
        if isinstance(self.backend, OpenAIResponsesBackend):
            heuristic = RefactoredSummariser(
                backend=HeuristicChunkBackend(),
                target_chars=self.target_chars,
                max_chars=self.max_chars,
                overlap_chars=self.overlap_chars,
                min_summary_chars=self.min_summary_chars,
            )
            return heuristic.summarise(text, doc_metadata=doc_metadata)
        return original_summarise(self, text, doc_metadata=doc_metadata)

    monkeypatch.setattr(
        "src.services.summariser_refactored.RefactoredSummariser.summarise",
        _fake_summarise,
    )
    monkeypatch.setattr(
        "src.services.summariser_refactored.OpenAIOneShotResponsesBackend.summarise_document",
        lambda self, **_: (_ for _ in ()).throw(
            SummarizationError("one-shot upstream failure")
        ),
    )
    monkeypatch.setattr(
        "src.services.summariser_refactored.CommonSenseSupervisor.validate",
        lambda self, **_: {"supervisor_passed": True},
    )

    _cli(
        [
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--api-key",
            "dummy-key",
        ]
    )

    summary_output = json.loads(output_path.read_text(encoding="utf-8"))
    summary_text = SummaryContract.from_mapping(summary_output).as_text()
    assert summary_text
    assert "blood pressure" in summary_text.lower()
    assert summary_output["metadata"]["summary_strategy_selected"] == "one_shot"
    assert summary_output["metadata"]["summary_strategy_used"] == "chunked"
    assert summary_output["metadata"]["summary_fast_lane_attempted"] is True
    assert summary_output["metadata"]["summary_heavy_lane_triggered"] is True


def test_cli_promotes_heuristic_candidate_when_openai_fails_supervisor(
    monkeypatch, tmp_path
) -> None:
    input_path = tmp_path / "payload.json"
    output_path = tmp_path / "summary.json"
    input_payload = {
        "text": (
            "Reason for Visit: Follow-up visit for low back pain after lifting storage boxes at work. "
            "History of Present Illness: The patient is improving but still has morning stiffness and pain with repeated lifting. "
            "Plan: Continue ibuprofen 400 mg as needed and return in two weeks."
        ),
        "metadata": {"job_id": "cli-test"},
    }
    input_path.write_text(json.dumps(input_payload), encoding="utf-8")

    def _build_contract(reason_line: str) -> Dict[str, Any]:
        return {
            "schema_version": "2025-10-01",
            "sections": [
                {
                    "slug": "patient_information",
                    "title": "Patient Information",
                    "content": "Not provided",
                    "ordinal": 1,
                    "kind": "context",
                },
                {
                    "slug": "billing_highlights",
                    "title": "Billing Highlights",
                    "content": "Not provided",
                    "ordinal": 2,
                    "kind": "context",
                },
                {
                    "slug": "legal_notes",
                    "title": "Legal / Notes",
                    "content": "Not provided",
                    "ordinal": 3,
                    "kind": "context",
                },
                {
                    "slug": "provider_seen",
                    "title": "Provider Seen",
                    "content": "Provider not documented.",
                    "ordinal": 4,
                    "kind": "mcc",
                },
                {
                    "slug": "reason_for_visit",
                    "title": "Reason for Visit",
                    "content": reason_line,
                    "ordinal": 5,
                    "kind": "mcc",
                },
                {
                    "slug": "clinical_findings",
                    "title": "Clinical Findings",
                    "content": "Lumbar tenderness and reduced range of motion were documented.",
                    "ordinal": 6,
                    "kind": "mcc",
                },
                {
                    "slug": "treatment_follow_up_plan",
                    "title": "Treatment / Follow-up Plan",
                    "content": "Continue ibuprofen and return in two weeks.",
                    "ordinal": 7,
                    "kind": "mcc",
                },
                {
                    "slug": "diagnoses",
                    "title": "Diagnoses",
                    "content": "- Low back pain",
                    "ordinal": 8,
                    "kind": "mcc",
                    "extra": {"items": ["Low back pain"]},
                },
                {
                    "slug": "healthcare_providers",
                    "title": "Healthcare Providers",
                    "content": "- Not listed.",
                    "ordinal": 9,
                    "kind": "mcc",
                    "extra": {"items": ["Provider not documented."]},
                },
                {
                    "slug": "medications",
                    "title": "Medications / Prescriptions",
                    "content": "- Ibuprofen 400 mg as needed",
                    "ordinal": 10,
                    "kind": "mcc",
                    "extra": {"items": ["Ibuprofen 400 mg as needed"]},
                },
            ],
            "_claims": [],
            "_evidence_spans": [],
            "metadata": {"source": "test"},
        }

    openai_summary = _build_contract(
        "Follow-up evaluation after a lifting injury with gradual symptomatic improvement."
    )
    heuristic_summary = _build_contract(
        "History of Present Illness: The patient is improving but still has morning stiffness and pain with repeated lifting."
    )
    observed_backends: list[str] = []

    def _fake_summarise(
        self, text: str, *, doc_metadata: Dict[str, Any] | None = None
    ) -> Dict[str, Any]:
        _ = text, doc_metadata
        observed_backends.append(type(self.backend).__name__)
        if isinstance(self.backend, OpenAIResponsesBackend):
            return openai_summary
        return heuristic_summary

    def _fake_validate(self, *, summary: Dict[str, Any], **_: Any) -> Dict[str, Any]:
        summary_text = SummaryContract.from_mapping(summary).as_text()
        passed = "History of Present Illness:" in summary_text
        return {
            "supervisor_passed": passed,
            "reason": "" if passed else "content_alignment_low",
            "length_score": 1.0,
            "content_alignment": 0.81 if passed else 0.782,
            "checks": {
                "length_ok": True,
                "structure_ok": True,
                "alignment_ok": passed,
            },
        }

    monkeypatch.setattr(
        "src.services.summariser_refactored.RefactoredSummariser.summarise",
        _fake_summarise,
    )
    monkeypatch.setattr(
        "src.services.summariser_refactored.CommonSenseSupervisor.validate",
        _fake_validate,
    )

    _cli(
        [
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--api-key",
            "dummy-key",
        ]
    )

    summary_output = json.loads(output_path.read_text(encoding="utf-8"))
    summary_text = SummaryContract.from_mapping(summary_output).as_text()
    assert "History of Present Illness:" in summary_text
    assert observed_backends.count("OpenAIResponsesBackend") == 4
    assert observed_backends.count("HeuristicChunkBackend") == 1


def test_cli_configures_structured_logging(monkeypatch, tmp_path) -> None:
    input_path = tmp_path / "payload.json"
    input_payload = {
        "text": (
            "Patient seen for follow-up. Blood pressure improving with medication adherence."
        ),
        "metadata": {"job_id": "cli-log-test"},
    }
    input_path.write_text(json.dumps(input_payload), encoding="utf-8")
    output_path = tmp_path / "summary.json"
    configure_calls: list[tuple[int, bool]] = []

    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setattr(
        "src.services.summariser_refactored.configure_logging",
        lambda *, level, force: configure_calls.append((level, force)),
    )
    monkeypatch.setattr(
        "src.services.summariser_refactored.CommonSenseSupervisor.validate",
        lambda self, **_: {"supervisor_passed": True},
    )

    _cli(
        [
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--dry-run",
        ]
    )

    assert configure_calls == [(logging.DEBUG, True)]
    assert output_path.exists()


def test_cli_uses_supervisor_retry_and_preserves_doc_metadata(
    monkeypatch, tmp_path
) -> None:
    input_payload = {
        "text": (
            "Follow-up visit for persistent low back pain after a lifting injury. "
            "Exam notes lumbar tenderness, reduced range of motion, and a plan for"
            " medication, stretching, and reassessment. "
        )
        * 6,
        "metadata": {
            "job_id": "cli-retry-test",
            "facility": "Retry Clinic",
            "pages": [
                {
                    "page_number": 1,
                    "text": (
                        "Follow-up visit for persistent low back pain after a lifting"
                        " injury with medication and stretching plan."
                    ),
                }
            ],
        },
    }
    input_path = tmp_path / "payload.json"
    input_path.write_text(json.dumps(input_payload), encoding="utf-8")
    output_path = tmp_path / "summary.json"

    def _build_contract(reason_line: str) -> Dict[str, Any]:
        return {
            "schema_version": "2025-10-01",
            "sections": [
                {
                    "slug": "patient_information",
                    "title": "Patient Information",
                    "content": "Not provided",
                    "ordinal": 1,
                    "kind": "context",
                },
                {
                    "slug": "billing_highlights",
                    "title": "Billing Highlights",
                    "content": "Not provided",
                    "ordinal": 2,
                    "kind": "context",
                },
                {
                    "slug": "legal_notes",
                    "title": "Legal / Notes",
                    "content": "Not provided",
                    "ordinal": 3,
                    "kind": "context",
                },
                {
                    "slug": "provider_seen",
                    "title": "Provider Seen",
                    "content": "Dr. Retry",
                    "ordinal": 4,
                    "kind": "mcc",
                },
                {
                    "slug": "reason_for_visit",
                    "title": "Reason for Visit",
                    "content": reason_line,
                    "ordinal": 5,
                    "kind": "mcc",
                },
                {
                    "slug": "clinical_findings",
                    "title": "Clinical Findings",
                    "content": "Lumbar tenderness and reduced range of motion were documented.",
                    "ordinal": 6,
                    "kind": "mcc",
                },
                {
                    "slug": "treatment_follow_up_plan",
                    "title": "Treatment / Follow-up Plan",
                    "content": "Continue ibuprofen, stretching, and return for reassessment.",
                    "ordinal": 7,
                    "kind": "mcc",
                },
                {
                    "slug": "diagnoses",
                    "title": "Diagnoses",
                    "content": "- Lumbar strain",
                    "ordinal": 8,
                    "kind": "mcc",
                    "extra": {"items": ["Lumbar strain"]},
                },
                {
                    "slug": "healthcare_providers",
                    "title": "Healthcare Providers",
                    "content": "- Dr. Retry",
                    "ordinal": 9,
                    "kind": "mcc",
                    "extra": {"items": ["Dr. Retry"]},
                },
                {
                    "slug": "medications",
                    "title": "Medications / Prescriptions",
                    "content": "- Ibuprofen 400 mg as needed",
                    "ordinal": 10,
                    "kind": "mcc",
                    "extra": {"items": ["Ibuprofen 400 mg as needed"]},
                },
            ],
            "_claims": [],
            "_evidence_spans": [],
            "metadata": {"source": "test"},
        }

    initial_summary = _build_contract("Initial draft summary without retry approval.")
    retry_summary = _build_contract(
        "Retry-approved follow-up for persistent low back pain after lifting injury."
    )
    observed_doc_metadata: list[Dict[str, Any] | None] = []

    def _fake_summarise(
        self, text: str, *, doc_metadata: Dict[str, Any] | None = None
    ) -> Dict[str, Any]:
        _ = self, text
        observed_doc_metadata.append(doc_metadata)
        return initial_summary if len(observed_doc_metadata) == 1 else retry_summary

    def _fake_validate(self, *, summary: Dict[str, Any], **_: Any) -> Dict[str, Any]:
        summary_text = SummaryContract.from_mapping(summary).as_text()
        passed = "Retry-approved" in summary_text
        return {
            "supervisor_passed": passed,
            "reason": "" if passed else "content_alignment_low",
            "length_score": 1.0,
            "content_alignment": 0.85 if passed else 0.2,
            "checks": {
                "length_ok": True,
                "structure_ok": True,
                "alignment_ok": passed,
            },
        }

    monkeypatch.setattr(
        "src.services.summariser_refactored.RefactoredSummariser.summarise",
        _fake_summarise,
    )
    monkeypatch.setattr(
        "src.services.summariser_refactored.CommonSenseSupervisor.validate",
        _fake_validate,
    )

    _cli(
        [
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--api-key",
            "dummy-key",
        ]
    )

    summary_output = json.loads(output_path.read_text(encoding="utf-8"))
    summary_text = SummaryContract.from_mapping(summary_output).as_text()
    assert "Retry-approved" in summary_text
    assert len(observed_doc_metadata) == 2
    for seen_metadata in observed_doc_metadata:
        assert seen_metadata is not None
        assert seen_metadata["job_id"] == input_payload["metadata"]["job_id"]
        assert seen_metadata["facility"] == input_payload["metadata"]["facility"]
        assert seen_metadata["pages"] == input_payload["metadata"]["pages"]
        if "_summary_page_filter_applied" in seen_metadata:
            assert seen_metadata["_summary_page_filter_applied"] is True
