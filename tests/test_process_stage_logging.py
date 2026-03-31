from __future__ import annotations

import types
from typing import Any, Dict, List

import pytest
from fastapi import HTTPException

from src.api.process import _execute_pipeline
from src.models.summary_contract import SummaryContract, SummarySection


class _FakeRequest:
    def __init__(self, *, state: Any, headers: Dict[str, str]):
        self.app = types.SimpleNamespace(state=state)
        self.headers = headers


class _StubOCRService:
    def process(self, file_bytes: bytes, trace_id: str | None = None) -> Dict[str, Any]:
        assert file_bytes.startswith(b"%PDF-")
        assert trace_id is not None
        return {
            "text": "A" * 400,
            "pages": [{"page_number": 1, "text": "content"}],
        }


class _StubSummariser:
    def _contract(self, text: str) -> Dict[str, Any]:
        assert text
        contract = SummaryContract(
            schema_version="test",
            sections=[
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
                    content="Dr. Unit Test",
                    ordinal=4,
                    kind="mcc",
                    extra={"items": ["Dr. Unit Test"]},
                ),
                SummarySection(
                    slug="reason_for_visit",
                    title="Reason for Visit",
                    content="Follow-up visit for lumbar strain after a lifting injury.",
                    ordinal=5,
                    kind="mcc",
                ),
                SummarySection(
                    slug="clinical_findings",
                    title="Clinical Findings",
                    content="Lumbar tenderness with reduced range of motion was documented.",
                    ordinal=6,
                    kind="mcc",
                ),
                SummarySection(
                    slug="treatment_follow_up_plan",
                    title="Treatment / Follow-up Plan",
                    content="Continue ibuprofen 400 mg as needed and return in two weeks.",
                    ordinal=7,
                    kind="mcc",
                ),
                SummarySection(
                    slug="diagnoses",
                    title="Diagnoses",
                    content="- Lumbar strain",
                    ordinal=8,
                    kind="mcc",
                    extra={"items": ["Lumbar strain"]},
                ),
                SummarySection(
                    slug="healthcare_providers",
                    title="Healthcare Providers",
                    content="- Dr. Unit Test",
                    ordinal=9,
                    kind="mcc",
                    extra={"items": ["Dr. Unit Test"]},
                ),
                SummarySection(
                    slug="medications",
                    title="Medications / Prescriptions",
                    content="- Ibuprofen 400 mg as needed",
                    ordinal=10,
                    kind="mcc",
                    extra={"items": ["Ibuprofen 400 mg as needed"]},
                ),
            ],
            claims=[],
            evidence_spans=[],
            metadata={"source": "stub"},
            claims_notice="stub",
        )
        return contract.to_dict()

    def summarise(self, text: str, *, doc_metadata: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return self._contract(text)

    async def summarise_async(
        self, text: str, *, doc_metadata: Dict[str, Any] | None = None
    ) -> Dict[str, Any]:
        return self._contract(text)


class _StubPDFWriter:
    def build(self, summary: Dict[str, Any]) -> bytes:
        assert summary.get("sections")
        return b"%PDF-1.4\nunit\n"


class _StubDriveClient:
    def upload_pdf(
        self, file_bytes: bytes, folder_id: str, *, log_context: Dict[str, Any]
    ) -> str:
        assert file_bytes
        assert folder_id
        assert "trace_id" in log_context
        assert log_context.get("request_id") == "req-unit"
        return "drive-abc123"


class _GuardPDFWriter:
    def build(self, summary: Dict[str, Any]) -> bytes:  # pragma: no cover - should not run
        raise AssertionError("pdf writer should not run after supervisor rejection")


class _GuardDriveClient:
    def upload_pdf(
        self, file_bytes: bytes, folder_id: str, *, log_context: Dict[str, Any]
    ) -> str:  # pragma: no cover - should not run
        raise AssertionError("drive upload should not run after supervisor rejection")


class _GuardOCRService:
    def process(
        self, file_bytes: bytes, trace_id: str | None = None
    ) -> Dict[str, Any]:  # pragma: no cover - should not run
        raise AssertionError("ocr should not run for readable native-text PDFs")


def _build_contract(
    *,
    provider: str,
    reason: str,
    findings: str,
    plan: str,
    diagnosis: str,
    medication: str,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    contract = SummaryContract(
        schema_version="test",
        sections=[
            SummarySection(
                slug="patient_information",
                title="Patient Information",
                content="Not provided",
                ordinal=1,
                kind="context",
            ),
            SummarySection(
                slug="provider_seen",
                title="Provider Seen",
                content=provider,
                ordinal=2,
                kind="mcc",
                extra={"items": [provider]},
            ),
            SummarySection(
                slug="reason_for_visit",
                title="Reason for Visit",
                content=reason,
                ordinal=3,
                kind="mcc",
            ),
            SummarySection(
                slug="clinical_findings",
                title="Clinical Findings",
                content=findings,
                ordinal=4,
                kind="mcc",
            ),
            SummarySection(
                slug="treatment_follow_up_plan",
                title="Treatment / Follow-up Plan",
                content=plan,
                ordinal=5,
                kind="mcc",
            ),
            SummarySection(
                slug="diagnoses",
                title="Diagnoses",
                content=f"- {diagnosis}",
                ordinal=6,
                kind="mcc",
                extra={"items": [diagnosis]},
            ),
            SummarySection(
                slug="healthcare_providers",
                title="Healthcare Providers",
                content=f"- {provider}",
                ordinal=7,
                kind="mcc",
                extra={"items": [provider]},
            ),
            SummarySection(
                slug="medications",
                title="Medications / Prescriptions",
                content=f"- {medication}",
                ordinal=8,
                kind="mcc",
                extra={"items": [medication]},
            ),
        ],
        claims=[],
        evidence_spans=[],
        metadata=metadata or {},
        claims_notice="stub",
    )
    return contract.to_dict()


class _ChunkedFallbackSummariser:
    def __init__(self) -> None:
        self.calls = 0

    def summarise(
        self, text: str, *, doc_metadata: Dict[str, Any] | None = None
    ) -> Dict[str, Any]:
        self.calls += 1
        return _build_contract(
            provider="Dr. Heavy Lane",
            reason="Follow-up visit for lumbar strain after a lifting injury.",
            findings="Lumbar tenderness improved with conservative treatment.",
            plan="Continue home stretching and return in two weeks.",
            diagnosis="Lumbar strain",
            medication="Ibuprofen 400 mg as needed",
            metadata={
                "summary_strategy_requested": "auto",
                "summary_strategy_selected": "one_shot",
                "summary_strategy_used": "chunked",
                "summary_route_reason": "within_operational_threshold_and_quality_budget",
            },
        )


class _AdaptiveFastLaneSummariser:
    def __init__(self) -> None:
        self.chunked_summariser = _ChunkedFallbackSummariser()

    def summarise_with_details(
        self, text: str, *, doc_metadata: Dict[str, Any] | None = None
    ) -> Any:
        assert text
        return types.SimpleNamespace(
            summary=_build_contract(
                provider="Dr. Fast Lane",
                reason="Administrative boilerplate and placeholders.",
                findings="Not documented.",
                plan="Not documented.",
                diagnosis="Lumbar strain",
                medication="Ibuprofen 400 mg as needed",
                metadata={
                    "summary_strategy_requested": "auto",
                    "summary_strategy_selected": "one_shot",
                    "summary_strategy_used": "one_shot",
                    "summary_route_reason": "within_operational_threshold_and_quality_budget",
                    "summary_route_metrics": {"estimated_tokens": 1200},
                },
            ),
            final_strategy="one_shot",
            route=types.SimpleNamespace(
                selected_strategy="one_shot",
                reason="within_operational_threshold_and_quality_budget",
            ),
        )


@pytest.mark.asyncio
async def test_execute_pipeline_emits_stage_events(monkeypatch):
    events: List[Dict[str, Any]] = []

    def _capture(_logger: Any, _level: int, event: str, **fields: Any) -> None:
        events.append({"event": event, "fields": fields})

    monkeypatch.setattr("src.api.process.structured_log", _capture)
    monkeypatch.setattr("src.utils.logging_utils.structured_log", _capture)
    monkeypatch.setenv("MIN_OCR_CHARS", "10")
    monkeypatch.setenv("WRITE_TO_DRIVE", "true")
    monkeypatch.setenv("MIN_SUMMARY_CHARS", "10")
    monkeypatch.setenv("MIN_SUMMARY_RATIO", "0.001")

    state = types.SimpleNamespace(
        config=types.SimpleNamespace(drive_report_folder_id="folder", project_id="proj"),
        stub_mode=False,
        supervisor_simple=True,
        ocr_service=_StubOCRService(),
        summariser=_StubSummariser(),
        pdf_writer=_StubPDFWriter(),
        drive_client=_StubDriveClient(),
    )
    request = _FakeRequest(
        state=state, headers={"X-Request-ID": "req-unit", "X-Cloud-Trace-Context": ""}
    )

    payload, validation, drive_id = await _execute_pipeline(
        request, pdf_bytes=b"%PDF-1.7\n1 0 obj", source="upload"
    )

    assert payload.startswith(b"%PDF-")
    assert validation.get("supervisor_passed") is True
    assert drive_id == "drive-abc123"

    stage_status: Dict[str, set[str]] = {}
    for entry in events:
        if entry["event"] != "pipeline_stage":
            continue
        stage = entry["fields"].get("stage")
        status = entry["fields"].get("status")
        if stage is None or status is None:
            continue
        stage_status.setdefault(stage, set()).add(status)

    expected_stages = {"ocr", "split", "summarisation", "pdf_write", "drive_upload", "supervisor"}
    assert expected_stages.issubset(stage_status.keys())
    for stage in expected_stages:
        assert stage_status[stage] == {"started", "completed"}


@pytest.mark.asyncio
async def test_execute_pipeline_rejects_quality_gate_failures(monkeypatch):
    monkeypatch.setenv("MIN_OCR_CHARS", "10")
    monkeypatch.setenv("WRITE_TO_DRIVE", "true")
    monkeypatch.setenv("MIN_SUMMARY_CHARS", "10")
    monkeypatch.setenv("MIN_SUMMARY_RATIO", "0.001")
    monkeypatch.setattr(
        "src.api.process.CommonSenseSupervisor.validate",
        lambda self, **_: {
            "supervisor_passed": False,
            "reason": "summary_quality_low",
            "length_score": 1.0,
            "content_alignment": 1.0,
            "checks": {
                "length_ok": True,
                "semantic_ok": True,
                "ratio_ok": True,
                "quality_ok": False,
            },
            "quality": {
                "reasons": ["mixed_section_content"],
                "mixed_sections": ["reason_for_visit"],
            },
        },
    )

    state = types.SimpleNamespace(
        config=types.SimpleNamespace(drive_report_folder_id="folder", project_id="proj"),
        stub_mode=False,
        supervisor_simple=True,
        ocr_service=_StubOCRService(),
        summariser=_StubSummariser(),
        pdf_writer=_GuardPDFWriter(),
        drive_client=_GuardDriveClient(),
    )
    request = _FakeRequest(
        state=state, headers={"X-Request-ID": "req-unit", "X-Cloud-Trace-Context": ""}
    )

    with pytest.raises(HTTPException) as exc_info:
        await _execute_pipeline(
            request, pdf_bytes=b"%PDF-1.7\n1 0 obj", source="upload"
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "Summary rejected by quality gate"


@pytest.mark.asyncio
async def test_execute_pipeline_skips_ocr_when_native_text_is_sufficient(monkeypatch):
    monkeypatch.setenv("MIN_OCR_CHARS", "10")
    monkeypatch.setenv("WRITE_TO_DRIVE", "true")
    monkeypatch.setenv("MIN_SUMMARY_CHARS", "10")
    monkeypatch.setenv("MIN_SUMMARY_RATIO", "0.001")
    monkeypatch.setattr(
        "src.api.process.prepare_summary_input_from_pdf_bytes",
        lambda *_args, **_kwargs: types.SimpleNamespace(
            requires_ocr=False,
            text_source="native_text",
            route_reason="native_text_sufficient",
            text=(
                "Follow-up visit for lumbar strain after a lifting injury. "
                "Lumbar tenderness improved with home stretching and ibuprofen. "
                "Continue conservative care and return in two weeks."
            ),
            pages=[
                {
                    "page_number": 1,
                    "text": (
                        "Follow-up visit for lumbar strain after a lifting injury. "
                        "Lumbar tenderness improved with home stretching and ibuprofen."
                    ),
                }
            ],
            metadata_patch={
                "summary_text_source": "native_text",
                "summary_requires_ocr": False,
                "summary_triage_reason": "native_text_sufficient",
                "summary_triage_metrics": {"page_count": 1},
            },
        ),
    )

    state = types.SimpleNamespace(
        config=types.SimpleNamespace(drive_report_folder_id="folder", project_id="proj"),
        stub_mode=False,
        supervisor_simple=True,
        ocr_service=_GuardOCRService(),
        summariser=_StubSummariser(),
        pdf_writer=_StubPDFWriter(),
        drive_client=_StubDriveClient(),
    )
    request = _FakeRequest(
        state=state, headers={"X-Request-ID": "req-unit", "X-Cloud-Trace-Context": ""}
    )

    payload, validation, drive_id = await _execute_pipeline(
        request, pdf_bytes=b"%PDF-1.4\nnative\n", source="upload"
    )

    assert payload.startswith(b"%PDF-")
    assert validation.get("supervisor_passed") is True
    assert drive_id == "drive-abc123"


@pytest.mark.asyncio
async def test_execute_pipeline_retries_chunked_after_fast_lane_rejection(monkeypatch):
    monkeypatch.setenv("MIN_OCR_CHARS", "10")
    monkeypatch.setenv("WRITE_TO_DRIVE", "true")
    monkeypatch.setenv("MIN_SUMMARY_CHARS", "10")
    monkeypatch.setenv("MIN_SUMMARY_RATIO", "0.001")

    def _validate(self, *, summary, **_kwargs):
        strategy = summary.get("metadata", {}).get("summary_strategy_used")
        passed = strategy == "chunked"
        return {
            "supervisor_passed": passed,
            "reason": "" if passed else "summary_quality_low",
            "length_score": 1.0,
            "content_alignment": 1.0,
            "checks": {
                "length_ok": True,
                "semantic_ok": True,
                "ratio_ok": True,
                "quality_ok": passed,
            },
            "quality": {
                "reasons": [] if passed else ["boilerplate_dominant_sections"],
                "mixed_sections": [],
            },
        }

    monkeypatch.setattr("src.api.process.CommonSenseSupervisor.validate", _validate)

    state = types.SimpleNamespace(
        config=types.SimpleNamespace(drive_report_folder_id="folder", project_id="proj"),
        stub_mode=False,
        supervisor_simple=True,
        ocr_service=_StubOCRService(),
        summariser=_AdaptiveFastLaneSummariser(),
        pdf_writer=_StubPDFWriter(),
        drive_client=_StubDriveClient(),
    )
    request = _FakeRequest(
        state=state, headers={"X-Request-ID": "req-unit", "X-Cloud-Trace-Context": ""}
    )

    payload, validation, drive_id = await _execute_pipeline(
        request, pdf_bytes=b"%PDF-1.7\n1 0 obj", source="upload"
    )

    assert payload.startswith(b"%PDF-")
    assert validation.get("supervisor_passed") is True
    assert drive_id == "drive-abc123"
    assert state.summariser.chunked_summariser.calls == 1


@pytest.mark.asyncio
async def test_execute_pipeline_fails_closed_when_chunked_retry_still_fails(monkeypatch):
    monkeypatch.setenv("MIN_OCR_CHARS", "10")
    monkeypatch.setenv("WRITE_TO_DRIVE", "true")
    monkeypatch.setenv("MIN_SUMMARY_CHARS", "10")
    monkeypatch.setenv("MIN_SUMMARY_RATIO", "0.001")
    monkeypatch.setattr(
        "src.api.process.CommonSenseSupervisor.validate",
        lambda self, **_kwargs: {
            "supervisor_passed": False,
            "reason": "summary_quality_low",
            "length_score": 1.0,
            "content_alignment": 1.0,
            "checks": {
                "length_ok": True,
                "semantic_ok": True,
                "ratio_ok": True,
                "quality_ok": False,
            },
            "quality": {
                "reasons": ["clinical_information_density_low"],
                "mixed_sections": [],
            },
        },
    )

    state = types.SimpleNamespace(
        config=types.SimpleNamespace(drive_report_folder_id="folder", project_id="proj"),
        stub_mode=False,
        supervisor_simple=True,
        ocr_service=_StubOCRService(),
        summariser=_AdaptiveFastLaneSummariser(),
        pdf_writer=_GuardPDFWriter(),
        drive_client=_GuardDriveClient(),
    )
    request = _FakeRequest(
        state=state, headers={"X-Request-ID": "req-unit", "X-Cloud-Trace-Context": ""}
    )

    with pytest.raises(HTTPException) as exc_info:
        await _execute_pipeline(
            request, pdf_bytes=b"%PDF-1.7\n1 0 obj", source="upload"
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "Summary rejected by quality gate"
    assert state.summariser.chunked_summariser.calls == 1
