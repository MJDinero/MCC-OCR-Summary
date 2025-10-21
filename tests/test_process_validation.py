import os
from fastapi.testclient import TestClient

from src.main import create_app


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
