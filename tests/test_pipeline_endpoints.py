import base64
import json

from fastapi.testclient import TestClient

from src.main import create_app
from src.services.pipeline import PipelineStatus


class StubWorkflowLauncher:
    def __init__(self):
        self.calls: list[dict] = []

    def launch(self, *, job, parameters=None, trace_context=None):
        self.calls.append({"job_id": job.job_id, "parameters": parameters, "trace": trace_context})
        return "executions/mock"


def _set_env(monkeypatch):
    monkeypatch.setenv("PROJECT_ID", "proj")
    monkeypatch.setenv("REGION", "us")
    monkeypatch.setenv("DOC_AI_PROCESSOR_ID", "pid")
    monkeypatch.setenv("DOC_AI_SPLITTER_PROCESSOR_ID", "split")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("DRIVE_INPUT_FOLDER_ID", "in")
    monkeypatch.setenv("DRIVE_REPORT_FOLDER_ID", "out")
    monkeypatch.setenv("INTAKE_GCS_BUCKET", "intake-test")
    monkeypatch.setenv("OUTPUT_GCS_BUCKET", "output-test")
    monkeypatch.setenv("SUMMARY_BUCKET", "summary-test")
    monkeypatch.setenv("PIPELINE_STATE_BACKEND", "memory")
    monkeypatch.setenv("SUMMARISER_JOB_NAME", "job-summary")
    monkeypatch.setenv("PDF_JOB_NAME", "job-pdf")
    monkeypatch.setenv("INTERNAL_EVENT_TOKEN", "secret-token")
    monkeypatch.setenv("PIPELINE_SERVICE_BASE_URL", "https://pipeline.test")
    monkeypatch.setenv("PIPELINE_DLQ_TOPIC", "projects/proj/topics/dlq")
    monkeypatch.setenv("SUMMARY_SCHEMA_VERSION", "2025-10-01")


def _build_app(monkeypatch):
    _set_env(monkeypatch)
    app = create_app()
    launcher = StubWorkflowLauncher()
    app.state.workflow_launcher = launcher
    return app, launcher


def _ingest_payload():
    return {
        "object": {
            "bucket": "intake-test",
            "name": "drive/file.pdf",
            "generation": "123",
            "metageneration": "1",
            "size": 1024,
            "md5Hash": "hash==",
        },
        "source": "drive-webhook",
        "trace_id": "abc123",
    }


def test_ingest_creates_job_and_dispatches_workflow(monkeypatch):
    app, launcher = _build_app(monkeypatch)
    client = TestClient(app)
    resp = client.post("/ingest", json=_ingest_payload())
    assert resp.status_code == 202
    body = resp.json()
    assert body["duplicate"] is False
    assert body["workflow_execution"] == "executions/mock"
    job_id = body["job_id"]
    assert launcher.calls and launcher.calls[0]["job_id"] == job_id
    params = launcher.calls[0]["parameters"]
    assert params["pipeline_service_base_url"] == "https://pipeline.test"
    assert params["internal_event_token"] == "secret-token"
    assert params["summariser_job_name"] == "job-summary"
    assert params["pdf_job_name"] == "job-pdf"
    assert params["intake_bucket"] == "intake-test"
    assert params["output_bucket"] == "output-test"
    assert params["summary_bucket"] == "summary-test"
    assert params["pipeline_dlq_topic"] == "projects/proj/topics/dlq"
    assert params["summary_schema_version"] == "2025-10-01"
    assert params["object_uri"].startswith("gs://intake-test/")
    job = app.state.state_store.get_job(job_id)
    assert job is not None
    assert job.status is PipelineStatus.WORKFLOW_DISPATCHED
    assert job.history[-1]["status"] == PipelineStatus.WORKFLOW_DISPATCHED.value


def test_ingest_returns_existing_job_on_duplicate(monkeypatch):
    app, _ = _build_app(monkeypatch)
    client = TestClient(app)
    payload = _ingest_payload()
    first = client.post("/ingest", json=payload)
    assert first.status_code == 202
    second = client.post("/ingest", json=payload)
    assert second.status_code == 412
    assert second.json()["duplicate"] is True
    assert second.json()["job_id"] == first.json()["job_id"]


def test_internal_event_updates_status(monkeypatch):
    app, _ = _build_app(monkeypatch)
    client = TestClient(app)
    ingest = client.post("/ingest", json=_ingest_payload())
    job_id = ingest.json()["job_id"]
    headers = {"X-Internal-Event-Token": "secret-token"}
    update_payload = {
        "status": "SUMMARY_DONE",
        "stage": "summary",
        "message": "summary complete",
        "extra": {"segments": 3},
        "metadataPatch": {"summary_uri": "gs://bucket/summary.json"},
    }
    resp = client.post(f"/internal/jobs/{job_id}/events", headers=headers, json=update_payload)
    assert resp.status_code == 200
    job = app.state.state_store.get_job(job_id)
    assert job.status is PipelineStatus.SUMMARY_DONE
    assert job.metadata["summary_uri"] == "gs://bucket/summary.json"
    assert job.history[-1]["stage"] == "summary"


def test_ingest_accepts_pubsub_envelope(monkeypatch):
    app, launcher = _build_app(monkeypatch)
    client = TestClient(app)
    gcs_data = {
        "bucket": "intake-test",
        "name": "drive/event.pdf",
        "generation": "456",
        "md5Hash": "hash==",
    }
    encoded = base64.b64encode(json.dumps(gcs_data).encode("utf-8")).decode("ascii")
    envelope = {
        "message": {
            "data": encoded,
            "attributes": {
                "bucketId": "intake-test",
                "objectId": "drive/event.pdf",
                "eventId": "evt-123",
            },
        },
        "subscription": "projects/proj/subscriptions/mock",
    }
    headers = {
        "ce-id": "ce-evt-123",
        "ce-traceparent": "00-1234567890abcdef1234567890abcdef-000000000000000f-01",
    }

    resp = client.post("/ingest", json=envelope, headers=headers)
    assert resp.status_code == 202
    body = resp.json()
    assert body["duplicate"] is False
    assert body["request_id"] == "ce-evt-123"
    assert launcher.calls and launcher.calls[0]["parameters"]["object_name"] == "drive/event.pdf"
    job = app.state.state_store.get_job(body["job_id"])
    assert job is not None
    assert job.object_uri.endswith("drive/event.pdf")
    assert job.trace_id == "1234567890abcdef1234567890abcdef"


def test_status_endpoint_returns_job(monkeypatch):
    app, _ = _build_app(monkeypatch)
    client = TestClient(app)
    ingest = client.post("/ingest", json=_ingest_payload())
    job_id = ingest.json()["job_id"]
    status = client.get(f"/status/{job_id}")
    assert status.status_code == 200
    data = status.json()
    assert data["job_id"] == job_id
    assert data["status"] == PipelineStatus.WORKFLOW_DISPATCHED.value
