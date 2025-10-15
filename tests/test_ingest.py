import base64
import hashlib

from fastapi.testclient import TestClient

from src.main import create_app


class _StubWorkflow:
    def launch(self, *, job, parameters=None, trace_context=None):
        return "executions/mock"


def _set_env(monkeypatch):
    monkeypatch.setenv("PROJECT_ID", "proj")
    monkeypatch.setenv("REGION", "us")
    monkeypatch.setenv("DOC_AI_PROCESSOR_ID", "pid")
    monkeypatch.setenv("DOC_AI_SPLITTER_PROCESSOR_ID", "split")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("DRIVE_INPUT_FOLDER_ID", "drive-in")
    monkeypatch.setenv("DRIVE_REPORT_FOLDER_ID", "drive-out")
    monkeypatch.setenv("PIPELINE_STATE_BACKEND", "memory")
    monkeypatch.setenv("INTAKE_GCS_BUCKET", "intake-test")
    monkeypatch.setenv("OUTPUT_GCS_BUCKET", "output-test")


def test_ingest_dedupe_key_contains_hash(monkeypatch):
    _set_env(monkeypatch)
    app = create_app()
    app.state.workflow_launcher = _StubWorkflow()
    client = TestClient(app)

    md5_bytes = hashlib.md5(b"dedupe-source").digest()
    md5_b64 = base64.b64encode(md5_bytes).decode("ascii")
    payload = {
        "object": {
            "bucket": "intake-test",
            "name": "inputs/report.pdf",
            "generation": "42",
            "md5Hash": md5_b64,
        },
        "trace_id": "trace-123",
    }

    resp = client.post("/ingest", json=payload)
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    job = app.state.state_store.get_job(job_id)
    assert job is not None
    assert "#" in job.dedupe_key
    base, hash_component = job.dedupe_key.split("#", 1)
    assert base == "intake-test/inputs/report.pdf@42"
    assert hash_component == hashlib.md5(b"dedupe-source").hexdigest()[:32]
    assert job.object_hash == hash_component

    status = client.get(f"/status/{job_id}")
    body = status.json()
    assert body["dedupe_key"] == job.dedupe_key
    assert body["object_hash"] == job.object_hash
