import os
from fastapi.testclient import TestClient

from src.main import create_app
from src.errors import SummarizationError, PDFGenerationError
from src.services import process_pipeline as pipeline_module


def _setup_env():
    os.environ["PROJECT_ID"] = "proj"
    os.environ["REGION"] = "us"
    os.environ["DOC_AI_PROCESSOR_ID"] = "pid"
    os.environ["OPENAI_API_KEY"] = "key"
    os.environ["DRIVE_INPUT_FOLDER_ID"] = "in"
    os.environ["DRIVE_REPORT_FOLDER_ID"] = "out"
    os.environ["PIPELINE_STATE_BACKEND"] = "memory"
    os.environ["SUMMARISER_JOB_NAME"] = "job-summary"
    os.environ["PDF_JOB_NAME"] = "job-pdf"
    os.environ["INTERNAL_EVENT_TOKEN"] = "token"


def test_ingest_validates_object_payload():
    _setup_env()
    app = create_app()
    client = TestClient(app)
    resp = client.post("/ingest", json={"object": {"bucket": "b"}})
    assert resp.status_code == 400
    assert "required" in resp.json()["detail"].lower()


def test_internal_event_rejects_missing_token():
    _setup_env()
    app = create_app()
    client = TestClient(app)
    ingest = client.post(
        "/ingest",
        json={
            "object": {"bucket": "b", "name": "doc.pdf", "generation": "1"},
            "trace_id": "t",
        },
    )
    job_id = ingest.json()["job_id"]
    resp = client.post(
        f"/ingest/internal/jobs/{job_id}/events",
        json={"status": "OCR_DONE"},
    )
    assert resp.status_code == 401


def test_summary_failure_triggers_pipeline_guard(monkeypatch):
    _setup_env()
    os.environ["STUB_MODE"] = "true"
    app = create_app()
    client = TestClient(app)

    async def _boom(_: str) -> dict[str, str]:
        raise SummarizationError("summary explosion")

    app.state.summariser.summarise_async = _boom  # type: ignore[attr-defined]
    published: dict[str, str] = {}

    def _fake_publish(**kwargs):
        published.update(kwargs)
        return True

    monkeypatch.setattr(pipeline_module, "publish_pipeline_failure", _fake_publish)

    resp = client.post(
        "/process",
        files={"file": ("doc.pdf", b"not-a-real-pdf", "application/pdf")},
    )
    assert resp.status_code == 502
    assert published["stage"] == "SUMMARY_JOB"
    assert published["trace_id"] is None
    os.environ.pop("STUB_MODE", None)


def test_pdf_failure_triggers_pipeline_guard(monkeypatch):
    _setup_env()
    os.environ["STUB_MODE"] = "true"
    app = create_app()
    client = TestClient(app)

    async def _stub_summary(_: str) -> dict[str, any]:  # type: ignore[override]
        return {
            "provider_seen": ["Encounter summary."],
            "reason_for_visit": ["Key point."],
            "clinical_findings": ["Finding."],
            "treatment_plan": ["Follow-up."],
            "_diagnoses_list": "Dx1",
            "_providers_list": "Dr Example",
            "_medications_list": "MedA",
        }

    class _BrokenWriter:
        def build(self, title, sections):
            raise PDFGenerationError("pdf failure")

    app.state.summariser.summarise_async = _stub_summary  # type: ignore[attr-defined]
    app.state.pdf_writer = _BrokenWriter()
    app.state.process_pipeline._pdf_writer = app.state.pdf_writer  # type: ignore[attr-defined]
    published: dict[str, str] = {}

    def _fake_publish(**kwargs):
        published.update(kwargs)
        return True

    monkeypatch.setattr(pipeline_module, "publish_pipeline_failure", _fake_publish)

    resp = client.post(
        "/process",
        files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert resp.status_code == 500
    assert published["stage"] == "PDF_JOB"
    os.environ.pop("STUB_MODE", None)
