import base64
import json

from fastapi.testclient import TestClient

from src.main import create_app


class _LauncherStub:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def launch(self, *, job, parameters=None, trace_context=None):
        self.calls.append(
            {
                "job": job,
                "parameters": parameters or {},
                "trace_context": trace_context,
            }
        )
        return "executions/test/123"


def _set_env(monkeypatch):
    monkeypatch.setenv("PROJECT_ID", "proj-123")
    monkeypatch.setenv("REGION", "us-test1")
    monkeypatch.setenv("DOC_AI_PROCESSOR_ID", "pid")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("DRIVE_INPUT_FOLDER_ID", "drive-in")
    monkeypatch.setenv("DRIVE_REPORT_FOLDER_ID", "drive-out")
    monkeypatch.setenv("SUMMARY_BUCKET", "summary-bucket")
    monkeypatch.setenv("OUTPUT_GCS_BUCKET", "output-bucket")
    monkeypatch.setenv("INTAKE_GCS_BUCKET", "intake-bucket")
    monkeypatch.setenv("PIPELINE_STATE_BACKEND", "memory")
    monkeypatch.setenv("INTERNAL_EVENT_TOKEN", "token")


def _build_client(monkeypatch):
    _set_env(monkeypatch)
    app = create_app()
    launcher = _LauncherStub()
    app.state.workflow_launcher = launcher
    client = TestClient(app)
    return app, client, launcher


def test_ingest_builds_argument_and_launches_workflow(monkeypatch):
    app, client, launcher = _build_client(monkeypatch)
    payload = {
        "object": {"bucket": "bucket-one", "name": "docs/file.pdf", "generation": "1"}
    }
    resp = client.post("/ingest", headers={"ce-id": "ce-abc"}, json=payload)
    assert resp.status_code == 202
    assert launcher.calls, "workflow launcher not invoked"
    params = launcher.calls[0]["parameters"]
    assert params["bucket"] == "bucket-one"
    assert params["object"] == "docs/file.pdf"
    assert params["trace_id"] == launcher.calls[0]["job"].trace_id
    assert params["intake_bucket"] == "intake-bucket"
    job = app.state.state_store.get_job(resp.json()["job_id"])
    assert job is not None
    assert job.object_uri == "gs://bucket-one/docs/file.pdf"


def test_ingest_validates_object_uri(monkeypatch):
    _, client, _ = _build_client(monkeypatch)
    resp = client.post("/ingest", json={"object": {"bucket": "missing-name"}})
    assert resp.status_code == 400


def test_ingest_propagates_trace_context_header(monkeypatch):
    _, client, launcher = _build_client(monkeypatch)
    headers = {"ce-id": "event-xyz", "X-Cloud-Trace-Context": "abcd1234/123;o=1"}
    payload = {"object": {"bucket": "trace", "name": "item.pdf"}}
    resp = client.post("/ingest", headers=headers, json=payload)
    assert resp.status_code == 202
    assert launcher.calls[0]["trace_context"] == "abcd1234/123;o=1"
    assert launcher.calls[0]["parameters"]["trace_id"] == "abcd1234"


def test_ingest_translates_traceparent_header(monkeypatch):
    _, client, launcher = _build_client(monkeypatch)
    trace_parent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    expected_span = str(int("00f067aa0ba902b7", 16))
    payload = {"object": {"bucket": "trace", "name": "item.pdf"}}
    resp = client.post(
        "/ingest", headers={"ce-traceparent": trace_parent}, json=payload
    )
    assert resp.status_code == 202
    trace_ctx = launcher.calls[0]["trace_context"]
    assert trace_ctx == f"4bf92f3577b34da6a3ce929d0e0e4736/{expected_span};o=1"


def test_ingest_passes_pipeline_service_base_url(monkeypatch):
    monkeypatch.setenv("PIPELINE_SERVICE_BASE_URL", "https://pipeline.test")
    _, client, launcher = _build_client(monkeypatch)
    payload = {"object": {"bucket": "bucket", "name": "report.pdf"}}
    resp = client.post("/ingest", json=payload)
    assert resp.status_code == 202
    assert (
        launcher.calls[0]["parameters"]["pipeline_service_base_url"]
        == "https://pipeline.test"
    )
    monkeypatch.delenv("PIPELINE_SERVICE_BASE_URL")


def test_ingest_extracts_bucket_from_message_attributes(monkeypatch):
    _, client, launcher = _build_client(monkeypatch)
    payload = {
        "message": {
            "attributes": {"bucketId": "attr-bucket", "objectId": "nested/path.pdf"}
        }
    }
    resp = client.post("/ingest", json=payload)
    assert resp.status_code == 202
    params = launcher.calls[0]["parameters"]
    assert params["bucket"] == "attr-bucket"
    assert params["object"] == "nested/path.pdf"


def test_ingest_extracts_bucket_from_base64_message(monkeypatch):
    _, client, launcher = _build_client(monkeypatch)
    nested = {"bucket": "payload-bucket", "name": "inner/path.pdf"}
    encoded = base64.b64encode(json.dumps(nested).encode("utf-8")).decode("ascii")
    payload = {"message": {"data": encoded, "attributes": {}}}
    resp = client.post("/ingest", json=payload)
    assert resp.status_code == 202
    params = launcher.calls[0]["parameters"]
    assert params["bucket"] == "payload-bucket"
    assert params["object"] == "inner/path.pdf"


def test_ingest_accepts_cloudevent_payload(monkeypatch):
    _, client, launcher = _build_client(monkeypatch)
    payload = {
        "id": "event-123",
        "data": {"bucket": "ce-bucket", "name": "doc.pdf"},
    }
    headers = {"ce-id": "event-123"}
    resp = client.post("/ingest", headers=headers, json=payload)
    assert resp.status_code == 202
    assert launcher.calls, "CloudEvent payload not accepted"


def test_ingest_returns_422_for_invalid_payload(monkeypatch):
    _, client, _ = _build_client(monkeypatch)
    resp = client.post("/ingest", data="not-json")
    assert resp.status_code == 400


def test_ingest_generates_unique_job_ids(monkeypatch):
    _, client, launcher = _build_client(monkeypatch)
    ids: set[str] = set()
    for idx in range(3):
        payload = {"object": {"bucket": "bucket", "name": f"file-{idx}.pdf"}}
        resp = client.post("/ingest", json=payload)
        assert resp.status_code == 202
        ids.add(resp.json()["job_id"])
    assert len(ids) == 3
    assert len(launcher.calls) == 3
