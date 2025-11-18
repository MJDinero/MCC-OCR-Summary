from __future__ import annotations

from typing import Any, Dict

import pytest

from src.errors import (
    DriveServiceError,
    PDFGenerationError,
    SummarizationError,
    ValidationError,
    PdfValidationError,
)
from src.services.process_pipeline import ProcessPipelineResult, ProcessPipelineService


class StubMetrics:
    def __init__(self) -> None:
        self.counter_calls: list[tuple[str, int, dict[str, str]]] = []

    def observe_latency(self, name: str, value: float, **labels: str) -> None:
        pass

    def increment(self, name: str, amount: int = 1, **labels: str) -> None:
        self.counter_calls.append((name, amount, labels))


class StubOCR:
    def __init__(self, text: str) -> None:
        self.text = text

    def process(self, pdf_bytes: bytes, *, trace_id: str | None = None) -> Dict[str, Any]:
        return {"text": self.text, "pages": [{"pageNumber": 1}]}


class StubSummariser:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self.payload = payload

    async def summarise_async(self, _: str) -> Dict[str, Any]:
        return self.payload


class StubWriter:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, Any]] = []

    def build(self, title: str, sections) -> bytes:
        if self.fail:
            raise PDFGenerationError("boom")
        self.calls.append((title, sections))
        return b"%PDF-sample"


class StubDelivery:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[bytes] = []

    def deliver_pdf(self, payload: bytes, **_: Any) -> str | None:
        if self.fail:
            raise DriveServiceError("drive broken")
        self.calls.append(payload)
        return "drive-file-id"


def _make_service(
    *,
    stub_mode: bool,
    summariser_payload: Dict[str, Any],
    pdf_writer: StubWriter | None = None,
    pdf_delivery: StubDelivery | None = None,
    ocr_text: str = "Extracted OCR text " * 50,
    metrics: StubMetrics | None = None,
) -> ProcessPipelineService:
    return ProcessPipelineService(
        ocr_service=StubOCR(ocr_text),
        summariser=StubSummariser(summariser_payload),
        pdf_writer=pdf_writer or StubWriter(),
        pdf_delivery=pdf_delivery or StubDelivery(),
        drive_report_folder_id="folder",
        stub_mode=stub_mode,
        supervisor_simple=True,
        summary_compose_mode="refactored",
        pdf_writer_mode="rich",
        writer_backend="ReportLab",
        metrics=metrics,
    )


@pytest.mark.asyncio
async def test_process_pipeline_happy_path(monkeypatch):
    service = _make_service(
        stub_mode=True,
        summariser_payload={
            "Medical Summary": "Patient evaluated and treated.",
            "provider_seen": ["Clinic visit"],
            "reason_for_visit": ["Follow-up requested."],
            "_diagnoses_list": "Dx",
            "_providers_list": "Dr Example",
            "_medications_list": "Med",
        },
    )
    result = await service.run(
        pdf_bytes=b"%PDF-1.4 sample",
        source="upload",
        trace_id="trace",
        guard_enabled=False,
        request_context={"path": "/process"},
    )
    assert isinstance(result, ProcessPipelineResult)
    assert result.drive_file_id == "drive-file-id"


@pytest.mark.asyncio
async def test_process_pipeline_rejects_empty_pdf():
    service = _make_service(
        stub_mode=True,
        summariser_payload={"Medical Summary": "ok"},
    )
    with pytest.raises(ValidationError):
        await service.run(
            pdf_bytes=b"",
            source="upload",
            trace_id=None,
            guard_enabled=False,
            request_context=None,
        )


@pytest.mark.asyncio
async def test_process_pipeline_validates_pdf_header():
    service = _make_service(
        stub_mode=False,
        summariser_payload={"Medical Summary": "Long enough summary."},
    )
    with pytest.raises(ValidationError):
        await service.run(
            pdf_bytes=b"not-a-pdf",
            source="upload",
            trace_id=None,
            guard_enabled=False,
            request_context=None,
        )


@pytest.mark.asyncio
async def test_run_summary_enforces_minimum_length(monkeypatch):
    metrics = StubMetrics()
    service = _make_service(
        stub_mode=False,
        summariser_payload={"Medical Summary": "short"},
        metrics=metrics,
    )
    with pytest.raises(SummarizationError):
        await service.run(
            pdf_bytes=b"%PDF-1.4 sample",
            source="upload",
            trace_id=None,
            guard_enabled=False,
            request_context=None,
        )
    assert any(name == "summarisation_failures" for name, _, _ in metrics.counter_calls)


@pytest.mark.asyncio
async def test_process_pipeline_rejects_short_ocr():
    service = _make_service(
        stub_mode=False,
        summariser_payload={"Medical Summary": "ok"},
        ocr_text="short text",
    )
    service._min_ocr_chars = 50
    with pytest.raises(ValidationError):
        await service.run(
            pdf_bytes=b"%PDF-1.4",
            source="upload",
            trace_id=None,
            guard_enabled=False,
            request_context=None,
        )


def test_supervise_output_guard_blocks(monkeypatch):
    metrics = StubMetrics()
    service = _make_service(
        stub_mode=True,
        summariser_payload={"Medical Summary": "ok"},
        metrics=metrics,
    )
    ocr_result = {"text": "sample text", "pages": [{}]}
    summary_dict = {
        "_canonical_sections": {
            "Provider Seen": ["(condensed) visit summary follows."]
        },
        "_canonical_entities": {},
    }
    pdf_bytes = b"%PDF"
    with pytest.raises(PdfValidationError):
        service._supervise_output(
            ocr_result=ocr_result,
            summary_dict=summary_dict,
            pdf_bytes=pdf_bytes,
            guard_enabled=True,
            trace_id="trace",
        )
    names = [name for name, _, _ in metrics.counter_calls]
    assert "pdf_validation_hits" in names
    assert "pdf_validation_blocks" in names


def test_build_pdf_handles_failures(monkeypatch):
    service = _make_service(
        stub_mode=True,
        summariser_payload={"Medical Summary": "ok"},
        pdf_writer=StubWriter(fail=True),
    )
    recorded: dict[str, Any] = {}

    def _fake_publish(**kwargs):
        recorded.update(kwargs)
        return True

    monkeypatch.setattr(
        "src.services.process_pipeline.publish_pipeline_failure", _fake_publish
    )
    with pytest.raises(PDFGenerationError):
        service._build_pdf(
            title="Doc",
            sections=[("Provider Seen", "Clinic")],
            trace_id="trace",
            source="upload",
        )
    assert recorded["stage"] == "PDF_JOB"


def test_deliver_pdf_respects_write_toggle(monkeypatch):
    service = _make_service(
        stub_mode=True,
        summariser_payload={"Medical Summary": "ok"},
    )
    monkeypatch.setenv("WRITE_TO_DRIVE", "false")
    assert service._deliver_pdf(payload=b"%PDF", trace_id=None, source="upload") is None


def test_deliver_pdf_handles_drive_errors():
    service_stub = _make_service(
        stub_mode=True,
        summariser_payload={"Medical Summary": "ok"},
        pdf_delivery=StubDelivery(fail=True),
    )
    assert service_stub._deliver_pdf(payload=b"%PDF", trace_id=None, source="upload") is None
    service = _make_service(
        stub_mode=False,
        summariser_payload={"Medical Summary": "ok"},
        pdf_delivery=StubDelivery(fail=True),
    )
    with pytest.raises(DriveServiceError):
        service._deliver_pdf(payload=b"%PDF", trace_id=None, source="upload")


def test_run_ocr_attaches_trace_id(monkeypatch):
    service = _make_service(
        stub_mode=True,
        summariser_payload={"Medical Summary": "ok"},
    )

    class _BrokenOCR:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def process(self, pdf_bytes: bytes, *, trace_id: str | None = None) -> dict[str, Any]:
            self.calls.append({"trace_id": trace_id})
            raise RuntimeError("ocr boom")

    service._ocr = _BrokenOCR()
    with pytest.raises(RuntimeError):
        service._run_ocr(pdf_bytes=b"%PDF", trace_id="trace", source="upload")
    assert service._ocr.calls[0]["trace_id"] == "trace"


def test_run_ocr_propagates_validation_error():
    service = _make_service(
        stub_mode=True,
        summariser_payload={"Medical Summary": "ok"},
    )

    class _ValidationOCR:
        def process(self, *_args, **_kwargs):
            raise ValidationError("invalid document")

    service._ocr = _ValidationOCR()
    with pytest.raises(ValidationError):
        service._run_ocr(pdf_bytes=b"%PDF", trace_id=None, source="upload")


def test_supervise_output_logs_validation(monkeypatch):
    service = _make_service(
        stub_mode=True,
        summariser_payload={"Medical Summary": "ok"},
    )

    class _StubSupervisor:
        def validate(self, **_: Any) -> dict[str, Any]:
            return {"supervisor_passed": False, "checks": {"length_ok": False}}

    service._supervisor = _StubSupervisor()
    validation, sections = service._supervise_output(
        ocr_result={"text": "ok", "pages": [{}]},
        summary_dict={"_canonical_sections": {"Provider Seen": ["Visit."]}},
        pdf_bytes=b"%PDF",
        guard_enabled=False,
        trace_id="trace",
    )
    assert validation["pdf_compliant"] is True
    assert sections


def test_supervise_output_records_hits_without_guard():
    service = _make_service(
        stub_mode=True,
        summariser_payload={"Medical Summary": "ok"},
    )
    validation, sections = service._supervise_output(
        ocr_result={"text": "ok", "pages": [{}]},
        summary_dict={"_canonical_sections": {"Provider Seen": ["(condensed)"]}},
        pdf_bytes=b"%PDF",
        guard_enabled=False,
        trace_id=None,
    )
    assert validation["pdf_compliant"] is False
    assert validation["pdf_forbidden_phrases"]
