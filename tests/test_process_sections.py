from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict

import pytest
from fastapi import HTTPException, status

from src.api import process
from src.services.process_pipeline import ProcessPipelineResult
from src.errors import (
    DriveServiceError,
    OCRServiceError,
    PDFGenerationError,
    PdfValidationError,
    SummarizationError,
    ValidationError,
)


def _section_map(sections: list[tuple[str, str]]) -> Dict[str, str]:
    return {heading: body for heading, body in sections}


def test_assemble_sections_uses_canonical_values() -> None:
    summarised: Dict[str, Any] = {
        "provider_seen": ["Facility context for this encounter."],
        "reason_for_visit": ["Primary complaint addressed.", "Follow-up arranged."],
        "clinical_findings": ["Vitals stable and imaging reviewed."],
        "treatment_follow_up_plan": ["Continue current medication regimen."],
        "_diagnoses_list": "Dx1\nDx2",
        "_providers_list": ["Dr. Example"],
        "_medications_list": "MedA 10 mg daily",
    }

    sections = process._assemble_sections(summarised)
    sections_dict = _section_map(sections)

    assert sections_dict["Provider Seen"] == "Facility context for this encounter."
    assert sections_dict["Reason for Visit"].startswith("- Primary complaint addressed.")
    assert sections_dict["Clinical Findings"].startswith("- Vitals stable")
    assert sections_dict["Treatment / Follow-up Plan"].startswith(
        "- Continue current medication regimen."
    )
    assert sections_dict["Diagnoses"].startswith("- Dx1")
    assert sections_dict["Healthcare Providers"].startswith("- Dr. Example")
    assert sections_dict["Medications / Prescriptions"].startswith("- MedA 10 mg daily")
    assert "Structured Indices" not in sections_dict
    assert "Structured content." not in sections_dict["Provider Seen"]


def test_assemble_sections_falls_back_when_empty() -> None:
    sections = process._assemble_sections({})
    expected = {
        "Provider Seen": "No provider was referenced in the record.",
        "Reason for Visit": "Reason for visit was not documented.",
        "Clinical Findings": "No clinical findings were highlighted.",
        "Treatment / Follow-up Plan": "No follow-up plan was identified.",
        "Diagnoses": "Not explicitly documented.",
        "Healthcare Providers": "Not listed.",
        "Medications / Prescriptions": "No medications recorded in extracted text.",
    }
    section_map = _section_map(sections)
    for heading, text in expected.items():
        assert section_map[heading] == text


def test_assemble_sections_includes_extra_sections() -> None:
    summarised = {
        "provider_seen": ["Clinic"],
        "_diagnoses_list": "Dx",
        "Custom Notes": "  Additional context  ",
    }
    sections = process._assemble_sections(summarised)
    assert ("Custom Notes", "Additional context") in sections


def test_pdf_validator_detects_forbidden_phrases() -> None:
    sections = [
        ("Provider Seen", "Document processed in 2 chunk(s). Overview text."),
        ("Reason for Visit", "- Stable vitals."),
    ]
    compliant, hits = process._validate_pdf_sections(
        sections, guard_enabled=False
    )
    assert not compliant
    assert "document processed in" in hits


def test_pdf_validator_guard_raises() -> None:
    sections = [
        ("Provider Seen", "Structured Indices appear here."),
    ]
    with pytest.raises(HTTPException):
        process._validate_pdf_sections(sections, guard_enabled=True)


def test_extract_trace_id_prefers_trace_header() -> None:
    request = SimpleNamespace(
        headers={
            "X-Cloud-Trace-Context": "1234567890abcdef1234567890abcdef/1;o=1",
            "X-Request-ID": "fallback",
        }
    )
    assert process._extract_trace_id(request) == "1234567890abcdef1234567890abcdef"
    fallback_request = SimpleNamespace(headers={"X-Request-ID": "req-1"})
    assert process._extract_trace_id(fallback_request) == "req-1"


def test_pdf_guard_enabled_respects_env(monkeypatch) -> None:
    monkeypatch.setenv("PDF_DEV_GUARD", "false")
    assert process._pdf_guard_enabled() is False
    monkeypatch.delenv("PDF_DEV_GUARD", raising=False)
    monkeypatch.setenv("PDF_GUARD_DISABLED", "1")
    assert process._pdf_guard_enabled() is False
    monkeypatch.delenv("PDF_GUARD_DISABLED", raising=False)
    monkeypatch.setenv("PDF_GUARD_ENABLED", "0")
    assert process._pdf_guard_enabled() is False
    monkeypatch.delenv("PDF_GUARD_ENABLED", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "dev")
    assert process._pdf_guard_enabled() is True


def test_mask_drive_id_formats_tokens() -> None:
    assert process._mask_drive_id("abcdefgh") == "ab***"
    assert process._mask_drive_id("abcdefghijkl") == "abcd***ijkl"
    assert process._mask_drive_id(None) is None


def test_drive_poll_batch_limit_respects_bounds(monkeypatch) -> None:
    monkeypatch.setenv("DRIVE_POLL_BATCH_LIMIT", "10")
    assert process._drive_poll_batch_limit() == 10
    monkeypatch.setenv("DRIVE_POLL_BATCH_LIMIT", "0")
    assert process._drive_poll_batch_limit() == 1
    monkeypatch.setenv("DRIVE_POLL_BATCH_LIMIT", "100")
    assert process._drive_poll_batch_limit() == 25
    monkeypatch.setenv("DRIVE_POLL_BATCH_LIMIT", "not-a-number")
    assert process._drive_poll_batch_limit() == 5
    monkeypatch.delenv("DRIVE_POLL_BATCH_LIMIT", raising=False)


def test_status_value_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("DRIVE_POLL_STATUS_KEY", " custom ")
    assert process._status_value("DRIVE_POLL_STATUS_KEY", "default") == "custom"
    monkeypatch.setenv("DRIVE_POLL_STATUS_KEY", "  ")
    assert process._status_value("DRIVE_POLL_STATUS_KEY", "fallback") == "fallback"
    monkeypatch.delenv("DRIVE_POLL_STATUS_KEY", raising=False)
    assert process._status_value("DRIVE_POLL_STATUS_KEY", "fallback") == "fallback"


def test_resolve_drive_input_folder(monkeypatch) -> None:
    cfg = SimpleNamespace(drive_input_folder_id="cfg-folder")
    monkeypatch.setenv("PDF_INPUT_FOLDER_ID", "pdf-folder")
    assert process._resolve_drive_input_folder(cfg) == "pdf-folder"
    monkeypatch.delenv("PDF_INPUT_FOLDER_ID", raising=False)
    monkeypatch.setenv("DRIVE_INPUT_FOLDER_ID", "drive-folder")
    assert process._resolve_drive_input_folder(cfg) == "drive-folder"
    monkeypatch.delenv("DRIVE_INPUT_FOLDER_ID", raising=False)
    assert process._resolve_drive_input_folder(cfg) == "cfg-folder"
    cfg_none = SimpleNamespace(drive_input_folder_id=None)
    with pytest.raises(HTTPException):
        process._resolve_drive_input_folder(cfg_none)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("UNIT_TESTING", "1")
    assert process._pdf_guard_enabled() is True
    monkeypatch.delenv("UNIT_TESTING", raising=False)
    assert process._pdf_guard_enabled() is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (ValidationError("bad payload"), 400),
        (PdfValidationError("pdf invalid"), 500),
        (SummarizationError("summary failed"), status.HTTP_502_BAD_GATEWAY),
        (PDFGenerationError("pdf failed"), 500),
        (DriveServiceError("drive exploded"), status.HTTP_502_BAD_GATEWAY),
        (OCRServiceError("ocr exploded"), status.HTTP_502_BAD_GATEWAY),
    ],
)
async def test_invoke_pipeline_maps_errors(error: Exception, status_code: int) -> None:
    class _StubPipeline:
        def __init__(self, exc: Exception) -> None:
            self.exc = exc

        async def run(self, **_: Any) -> Any:
            raise self.exc

    request = SimpleNamespace(
        headers={},
        app=SimpleNamespace(
            state=SimpleNamespace(process_pipeline=_StubPipeline(error))
        ),
        url=SimpleNamespace(path="/process"),
        method="POST",
    )

    with pytest.raises(HTTPException) as excinfo:
        await process._invoke_pipeline(
            request, pdf_bytes=b"%PDF", source="upload", trace_id="trace"
        )
    assert excinfo.value.status_code == status_code


@pytest.mark.asyncio
async def test_invoke_pipeline_requires_pipeline() -> None:
    request = SimpleNamespace(
        headers={},
        app=SimpleNamespace(state=SimpleNamespace(process_pipeline=None)),
        url=SimpleNamespace(path="/process"),
        method="POST",
    )
    with pytest.raises(HTTPException) as excinfo:
        await process._invoke_pipeline(
            request, pdf_bytes=b"%PDF", source="upload", trace_id="trace"
        )
    assert excinfo.value.status_code == 500


@pytest.mark.asyncio
async def test_health_check_returns_ok():
    response = await process.health_check(SimpleNamespace())
    assert response.status_code == 200
    assert json.loads(response.body.decode("utf-8")) == {"status": "ok"}


@pytest.mark.asyncio
async def test_process_pdf_streams_response(monkeypatch):
    async def _fake_invoke(request, *, pdf_bytes, source, trace_id):
        return ProcessPipelineResult(
            pdf_bytes=b"%PDF-data",
            validation={},
            drive_file_id="drive",
        )

    monkeypatch.setattr(process, "_invoke_pipeline", _fake_invoke)

    class _Upload:
        async def read(self) -> bytes:
            return b"%PDF-1.4"

    request = SimpleNamespace(
        headers={},
        app=SimpleNamespace(state=SimpleNamespace(summary_compose_mode="refactored")),
        url=SimpleNamespace(path="/process"),
        method="POST",
    )
    response = await process.process_pdf(request, _Upload())
    assert response.media_type == "application/pdf"
    assert response.body == b"%PDF-data"


@pytest.mark.asyncio
async def test_process_drive_invokes_pipeline(monkeypatch) -> None:
    class _StubDrive:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def download_pdf(self, file_id: str, *, log_context: dict[str, Any], quota_project: str | None):
            self.calls.append({"file_id": file_id, "trace": log_context["trace_id"]})
            return b"%PDF"

    class _StubPipeline:
        async def run(self, **_: Any) -> ProcessPipelineResult:
            return ProcessPipelineResult(
                pdf_bytes=b"%PDF",
                validation={"pdf_compliant": True, "supervisor_passed": True},
                drive_file_id="drive-output",
            )

    state = SimpleNamespace(
        config=SimpleNamespace(project_id="proj"),
        drive_client=_StubDrive(),
        process_pipeline=_StubPipeline(),
        summary_compose_mode="refactored",
        writer_backend="reportlab",
        pdf_writer=SimpleNamespace(
            backend=SimpleNamespace(__class__=type("ReportLabBackend", (), {}))
        ),
    )
    request = SimpleNamespace(
        headers={"X-Cloud-Trace-Context": "abc123/1;o=1"},
        app=SimpleNamespace(state=state),
        url=SimpleNamespace(path="/process/drive"),
        method="GET",
    )
    response = await process.process_drive(request, file_id="drive-source")
    assert response.status_code == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["report_file_id"] == "drive-output"
    assert payload["pdf_compliant"] is True


@pytest.mark.asyncio
async def test_process_drive_handles_validation_error(monkeypatch) -> None:
    class _ErrorDrive:
        def download_pdf(self, *_, **__):
            raise ValidationError("bad file id")

    request = SimpleNamespace(
        headers={},
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(project_id="proj"),
                drive_client=_ErrorDrive(),
                process_pipeline=SimpleNamespace(run=lambda **_: None),
            )
        ),
    )
    with pytest.raises(HTTPException) as excinfo:
        await process.process_drive(request, file_id="bad")
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_process_drive_handles_drive_error(monkeypatch) -> None:
    class _Drive:
        def download_pdf(self, *_, **__):
            raise DriveServiceError("drive offline")

    request = SimpleNamespace(
        headers={},
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(project_id="proj"),
                drive_client=_Drive(),
                process_pipeline=SimpleNamespace(run=lambda **_: None),
            )
        ),
    )
    with pytest.raises(HTTPException) as excinfo:
        await process.process_drive(request, file_id="bad")
    assert excinfo.value.status_code == 502


@pytest.mark.asyncio
async def test_process_drive_requires_drive_upload(monkeypatch) -> None:
    class _StubPipeline:
        async def run(self, **_: Any) -> ProcessPipelineResult:
            return ProcessPipelineResult(
                pdf_bytes=b"%PDF",
                validation={"supervisor_passed": True},
                drive_file_id=None,
            )

    request = SimpleNamespace(
        headers={},
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(project_id="proj"),
                drive_client=SimpleNamespace(download_pdf=lambda *args, **kwargs: b"%PDF"),
                process_pipeline=_StubPipeline(),
                summary_compose_mode="refactored",
                pdf_writer=SimpleNamespace(backend=SimpleNamespace(__class__=type("Backend", (), {}))),
            )
        ),
        url=SimpleNamespace(path="/process/drive"),
        method="GET",
    )
    with pytest.raises(HTTPException) as excinfo:
        await process.process_drive(request, file_id="test")
    assert excinfo.value.status_code == 503


@pytest.mark.asyncio
async def test_process_drive_exposes_validation_details(monkeypatch) -> None:
    class _StubPipeline:
        async def run(self, **_: Any) -> ProcessPipelineResult:
            return ProcessPipelineResult(
                pdf_bytes=b"%PDF",
                validation={
                    "supervisor_passed": False,
                    "pdf_compliant": False,
                    "pdf_forbidden_phrases": ["document processed in"],
                },
                drive_file_id="drive-result",
            )

    drive_client = SimpleNamespace(download_pdf=lambda *args, **kwargs: b"%PDF")
    class ReportLabBackend:
        pass

    pdf_writer = SimpleNamespace(backend=ReportLabBackend())
    state = SimpleNamespace(
        config=SimpleNamespace(project_id="proj"),
        drive_client=drive_client,
        process_pipeline=_StubPipeline(),
        summary_compose_mode="refactored",
        pdf_writer=pdf_writer,
    )
    request = SimpleNamespace(
        headers={},
        app=SimpleNamespace(state=state),
        url=SimpleNamespace(path="/process/drive"),
        method="GET",
    )
    response = await process.process_drive(request, file_id="ok")
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["pdf_compliant"] is False
    assert payload["pdf_forbidden_phrases"] == ["document processed in"]
    assert payload["writer_backend"] == "ReportLabBackend"


@pytest.mark.asyncio
async def test_drive_poll_requires_token(monkeypatch) -> None:
    state = SimpleNamespace(
        internal_event_token="secret-token",
        config=SimpleNamespace(drive_input_folder_id="folder"),
        drive_client=SimpleNamespace(),
    )
    request = SimpleNamespace(
        headers={},
        query_params={},
        app=SimpleNamespace(state=state),
        url=SimpleNamespace(path="/process/drive/poll"),
        method="POST",
    )
    with pytest.raises(HTTPException) as excinfo:
        await process.poll_drive_folder(request, limit=1)
    assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_drive_poll_processes_files(monkeypatch) -> None:
    pending = [{"id": "drive-source", "name": "Doc"}]
    monkeypatch.setattr(
        process.drive_client_module,
        "list_pending_pdfs",
        lambda *args, **kwargs: pending,
    )
    updates: list[tuple[str, dict]] = []

    def _record_update(file_id: str, props: dict):
        updates.append((file_id, props))
        return {"id": file_id, "appProperties": props}

    monkeypatch.setattr(process.drive_client_module, "update_app_properties", _record_update)

    async def _pipeline(*_args, **_kwargs):
        return ProcessPipelineResult(
            pdf_bytes=b"%PDF",
            validation={"supervisor_passed": True},
            drive_file_id="drive-report",
        )

    monkeypatch.setattr(process, "_invoke_pipeline", _pipeline)

    class _DriveClient:
        def download_pdf(self, file_id: str, **_kwargs: Any) -> bytes:
            assert file_id == "drive-source"
            return b"%PDF"

    state = SimpleNamespace(
        internal_event_token="secret-token",
        config=SimpleNamespace(drive_input_folder_id="folder", project_id="proj"),
        drive_client=_DriveClient(),
    )
    request = SimpleNamespace(
        headers={"X-Internal-Event-Token": "secret-token"},
        query_params={},
        app=SimpleNamespace(state=state),
        url=SimpleNamespace(path="/process/drive/poll"),
        method="POST",
    )

    response = await process.poll_drive_folder(request, limit=1)
    body = json.loads(response.body.decode("utf-8"))
    assert body["status"] == "processed"
    assert body["processed"] and body["processed"][0]["file_id"] == "drive-source"
    statuses = [props["mccStatus"] for _, props in updates if "mccStatus" in props]
    assert "processing" in statuses and "completed" in statuses
    assert body["skipped"] == []


@pytest.mark.asyncio
async def test_drive_poll_records_failures(monkeypatch) -> None:
    pending = [{"id": "drive-error", "name": "Doc"}]
    monkeypatch.setattr(
        process.drive_client_module,
        "list_pending_pdfs",
        lambda *args, **kwargs: pending,
    )
    updates: list[dict] = []

    def _record_update(file_id: str, props: dict):
        updates.append(props)
        return {"id": file_id, "appProperties": props}

    monkeypatch.setattr(process.drive_client_module, "update_app_properties", _record_update)

    async def _pipeline(*_args, **_kwargs):
        raise HTTPException(status_code=500, detail="boom")

    monkeypatch.setattr(process, "_invoke_pipeline", _pipeline)

    class _DriveClient:
        def download_pdf(self, *_args, **_kwargs):
            return b"%PDF"

    state = SimpleNamespace(
        internal_event_token="secret-token",
        config=SimpleNamespace(drive_input_folder_id="folder"),
        drive_client=_DriveClient(),
    )
    request = SimpleNamespace(
        headers={"X-Internal-Event-Token": "secret-token"},
        query_params={},
        app=SimpleNamespace(state=state),
        url=SimpleNamespace(path="/process/drive/poll"),
        method="POST",
    )

    response = await process.poll_drive_folder(request, limit=1)
    body = json.loads(response.body.decode("utf-8"))
    assert body["status"] == "failed"
    assert body["errors"] and body["errors"][0]["file_id"] == "drive-error"
    assert any(props.get("mccStatus") == "failed" for props in updates)
    assert body["skipped"] == []


@pytest.mark.asyncio
async def test_drive_poll_idle_when_no_files(monkeypatch) -> None:
    monkeypatch.setattr(
        process.drive_client_module,
        "list_pending_pdfs",
        lambda *args, **kwargs: [],
    )
    state = SimpleNamespace(
        internal_event_token="secret-token",
        config=SimpleNamespace(drive_input_folder_id="folder"),
        drive_client=SimpleNamespace(),
    )
    request = SimpleNamespace(
        headers={"X-Internal-Event-Token": "secret-token"},
        query_params={},
        app=SimpleNamespace(state=state),
        url=SimpleNamespace(path="/process/drive/poll"),
        method="POST",
    )
    response = await process.poll_drive_folder(request, limit=1)
    body = json.loads(response.body.decode("utf-8"))
    assert body["status"] == "idle"
    assert body["polled"] == 0
    assert body["skipped"] == []


@pytest.mark.asyncio
async def test_drive_poll_handles_listing_error(monkeypatch) -> None:
    def _raise(*_args, **_kwargs):
        raise RuntimeError("drive offline")

    monkeypatch.setattr(process.drive_client_module, "list_pending_pdfs", _raise)
    state = SimpleNamespace(
        internal_event_token="secret-token",
        config=SimpleNamespace(drive_input_folder_id="folder"),
        drive_client=SimpleNamespace(),
    )
    request = SimpleNamespace(
        headers={"X-Internal-Event-Token": "secret-token"},
        query_params={},
        app=SimpleNamespace(state=state),
        url=SimpleNamespace(path="/process/drive/poll"),
        method="POST",
    )
    with pytest.raises(HTTPException) as excinfo:
        await process.poll_drive_folder(request, limit=1)
    assert excinfo.value.status_code == 502


@pytest.mark.asyncio
async def test_drive_poll_claim_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        process.drive_client_module,
        "list_pending_pdfs",
        lambda *args, **kwargs: [{"id": "drive-claim", "name": "Doc"}],
    )

    def _raise(*_args, **_kwargs):
        raise RuntimeError("claim failed")

    monkeypatch.setattr(process.drive_client_module, "update_app_properties", _raise)
    state = SimpleNamespace(
        internal_event_token="secret-token",
        config=SimpleNamespace(drive_input_folder_id="folder"),
        drive_client=SimpleNamespace(),
    )
    request = SimpleNamespace(
        headers={"X-Internal-Event-Token": "secret-token"},
        query_params={},
        app=SimpleNamespace(state=state),
        url=SimpleNamespace(path="/process/drive/poll"),
        method="POST",
    )
    response = await process.poll_drive_folder(request, limit=1)
    body = json.loads(response.body.decode("utf-8"))
    assert body["status"] == "failed"
    assert body["errors"][0]["error"].startswith("claim_failed")
    assert body["skipped"] == []


@pytest.mark.asyncio
async def test_drive_poll_skips_summary_artifacts(monkeypatch) -> None:
    monkeypatch.setattr(
        process.drive_client_module,
        "list_pending_pdfs",
        lambda *args, **kwargs: [
            {"id": "summary-file", "name": "summary-test.pdf", "appProperties": {}}
        ],
    )
    state = SimpleNamespace(
        internal_event_token="secret-token",
        config=SimpleNamespace(drive_input_folder_id="folder"),
        drive_client=SimpleNamespace(),
    )
    request = SimpleNamespace(
        headers={"X-Internal-Event-Token": "secret-token"},
        query_params={},
        app=SimpleNamespace(state=state),
        url=SimpleNamespace(path="/process/drive/poll"),
        method="POST",
    )
    response = await process.poll_drive_folder(request, limit=1)
    body = json.loads(response.body.decode("utf-8"))
    assert body["processed"] == []
    assert body["errors"] == []
    assert body["skipped"] and body["skipped"][0]["file_id"] == "summary-file"
