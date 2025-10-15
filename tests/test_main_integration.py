from fastapi.testclient import TestClient

from src.main import create_app


class StubLauncher:
    def launch(self, *, job, parameters=None, trace_context=None):
        return "exec/mock"


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
