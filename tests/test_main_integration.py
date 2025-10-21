import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

from src.main import create_app


class StubLauncher:
    def launch(self, *, job, parameters=None, trace_context=None):
        return "exec/mock"


class StubSummariser:
    chunk_target_chars = 1200
    chunk_hard_max = 1800

    def summarise(self, text: str):  # pragma: no cover - simple stub
        return {"Medical Summary": "Refactored summary output " + ("-" * 520)}

    async def summarise_async(self, text: str):  # pragma: no cover - reuse sync result
        return self.summarise(text)


def test_health_and_ingest(monkeypatch):
    monkeypatch.setenv("PROJECT_ID", "proj")
    monkeypatch.setenv("REGION", "us")
    monkeypatch.setenv("DOC_AI_PROCESSOR_ID", "pid")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("DRIVE_INPUT_FOLDER_ID", "in")
    monkeypatch.setenv("DRIVE_REPORT_FOLDER_ID", "out")
    monkeypatch.setenv("PIPELINE_STATE_BACKEND", "memory")
    monkeypatch.setenv("SUMMARISER_JOB_NAME", "job-summary")
    monkeypatch.setenv("PDF_JOB_NAME", "job-pdf")
    monkeypatch.setenv("INTERNAL_EVENT_TOKEN", "token")

    app = create_app()
    app.state.workflow_launcher = StubLauncher()
    app.state.summariser = StubSummariser()
    client = TestClient(app)

    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

    payload = {
        "object": {"bucket": "b", "name": "doc.pdf", "generation": "1"},
        "trace_id": "trace",
    }
    resp = client.post("/ingest", json=payload)
    assert resp.status_code == 202
    body = resp.json()
    assert body["workflow_execution"] == "exec/mock"
    summary = app.state.summariser.summarise("sample text")
    summary_text = summary.get("Medical Summary", "")
    assert len(summary_text) >= 500
