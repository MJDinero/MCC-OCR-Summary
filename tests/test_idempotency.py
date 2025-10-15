import base64
import hashlib

from fastapi.testclient import TestClient

from src.main import create_app


def _set_env(monkeypatch):
    monkeypatch.setenv("PROJECT_ID", "proj")
    monkeypatch.setenv("REGION", "us")
    monkeypatch.setenv("DOC_AI_PROCESSOR_ID", "pid")
    monkeypatch.setenv("DOC_AI_SPLITTER_PROCESSOR_ID", "split")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("DRIVE_INPUT_FOLDER_ID", "drive-in")
    monkeypatch.setenv("DRIVE_REPORT_FOLDER_ID", "drive-out")
    monkeypatch.setenv("PIPELINE_STATE_BACKEND", "memory")
    monkeypatch.setenv("INTAKE_GCS_BUCKET", "intake")
    monkeypatch.setenv("OUTPUT_GCS_BUCKET", "output")
    monkeypatch.setenv("SUMMARY_BUCKET", "output")
    monkeypatch.setenv("INTERNAL_EVENT_TOKEN", "token")


def _payload(md5_bytes):
    return {
        "object": {
            "bucket": "intake",
            "name": "docs/file.pdf",
            "generation": "7",
            "md5Hash": base64.b64encode(md5_bytes).decode("ascii"),
        }
    }


def test_idempotency_includes_hash(monkeypatch):
    _set_env(monkeypatch)
    app = create_app()
    app.state.workflow_launcher = lambda **_: None  # type: ignore[assignment]
    client = TestClient(app)

    first = client.post("/ingest", json=_payload(hashlib.md5(b"alpha").digest()))
    second = client.post("/ingest", json=_payload(hashlib.md5(b"beta").digest()))
    third = client.post("/ingest", json=_payload(hashlib.md5(b"beta").digest()))

    assert first.status_code == 202
    assert second.status_code == 202
    assert third.status_code == 412

    first_job = first.json()["job_id"]
    second_job = second.json()["job_id"]
    assert first_job != second_job  # different hashes → different dedupe keys
    assert third.json()["job_id"] == second_job  # identical hash → duplicate
