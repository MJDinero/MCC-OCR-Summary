from __future__ import annotations

import types
from typing import Any, Dict, List

import pytest

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
                    slug="medical_summary",
                    title="Medical Summary",
                    content="B" * 400,
                    ordinal=1,
                    kind="narrative",
                )
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
