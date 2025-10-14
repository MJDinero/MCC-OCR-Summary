import base64
import json
import uuid
from types import SimpleNamespace
from typing import Any, Dict, List

from fastapi.testclient import TestClient

from src import api_ingest


class _StubExecutionsClient:
    def __init__(self) -> None:
        self.requests: List[Dict[str, Any]] = []

    def create_execution(self, request: Dict[str, Any], retry=None):
        self.requests.append({"request": request, "retry": retry})
        return SimpleNamespace(name="projects/test/locations/test/workflows/docai/executions/123")


def _setup_env(monkeypatch):
    monkeypatch.setenv("PROJECT_ID", "proj-123")
    monkeypatch.setenv("REGION", "us-test1")
    monkeypatch.setenv("WORKFLOW_NAME", "wf-test")
    monkeypatch.delenv("INTAKE_GCS_BUCKET", raising=False)
    monkeypatch.delenv("INTAKE_BUCKET", raising=False)
    monkeypatch.delenv("OUTPUT_GCS_BUCKET", raising=False)
    monkeypatch.delenv("OUTPUT_BUCKET", raising=False)
    monkeypatch.delenv("SUMMARY_BUCKET", raising=False)


def test_ingest_builds_argument_and_launches_workflow(monkeypatch):
    _setup_env(monkeypatch)
    stub = _StubExecutionsClient()
    monkeypatch.setattr(api_ingest, "_wf_client", stub)

    uuid_values = iter(
        [
            uuid.UUID("00000000-0000-4000-8000-000000000001"),
            uuid.UUID("00000000-0000-4000-8000-000000000002"),
        ]
    )
    monkeypatch.setattr(api_ingest, "uuid4", lambda: next(uuid_values))

    client = TestClient(api_ingest.app)
    payload = {"id": "event-123", "data": {"bucket": "sample-bucket", "name": "docs/file.pdf"}}

    resp = client.post("/ingest", headers={"ce-id": "ce-abc"}, json=payload)
    assert resp.status_code == 200

    body = resp.json()
    assert body["ok"] is True
    assert body["job_id"] == "00000000-0000-4000-8000-000000000001"
    assert body["object_uri"] == "gs://sample-bucket/docs/file.pdf"
    assert body["execution"] == "projects/test/locations/test/workflows/docai/executions/123"

    assert stub.requests, "Expected workflow execution request to be recorded"
    execution_request = stub.requests[0]["request"]
    assert execution_request["parent"] == "projects/proj-123/locations/us-test1/workflows/wf-test"
    execution_argument = json.loads(execution_request["execution"]["argument"])
    assert execution_argument["job_id"] == "00000000-0000-4000-8000-000000000001"
    assert execution_argument["trace_id"] == "ce-abc"
    assert execution_argument["request_id"] == "ce-abc"
    assert execution_argument["dedupe_key"] == "ce-abc"
    assert execution_argument["object_uri"] == "gs://sample-bucket/docs/file.pdf"
    assert execution_argument["intake_bucket"] == "sample-bucket"
    assert execution_argument["gcs_uri"] == "gs://sample-bucket/docs/file.pdf"
    assert "trace_context" not in execution_argument


def test_ingest_validates_object_uri(monkeypatch):
    _setup_env(monkeypatch)
    stub = _StubExecutionsClient()
    monkeypatch.setattr(api_ingest, "_wf_client", stub)

    client = TestClient(api_ingest.app)
    resp = client.post("/ingest", json={"data": {"bucket": "missing-name"}})
    assert resp.status_code == 400
    assert stub.requests == []


def test_ingest_propagates_trace_context_header(monkeypatch):
    _setup_env(monkeypatch)
    stub = _StubExecutionsClient()
    monkeypatch.setattr(api_ingest, "_wf_client", stub)

    uuid_values = iter(
        [
            uuid.UUID("00000000-0000-4000-8000-000000000100"),
            uuid.UUID("00000000-0000-4000-8000-000000000101"),
        ]
    )
    monkeypatch.setattr(api_ingest, "uuid4", lambda: next(uuid_values))

    client = TestClient(api_ingest.app)
    headers = {"ce-id": "event-xyz", "X-Cloud-Trace-Context": "abcd1234ef567890/123456;o=1"}
    payload = {"data": {"bucket": "trace-bucket", "name": "item.pdf"}}

    resp = client.post("/ingest", headers=headers, json=payload)
    assert resp.status_code == 200

    execution_argument = json.loads(stub.requests[0]["request"]["execution"]["argument"])
    assert execution_argument["trace_context"] == "abcd1234ef567890/123456;o=1"
    assert execution_argument["trace_id"] == "abcd1234ef567890"
    assert execution_argument["job_id"] == "00000000-0000-4000-8000-000000000100"


def test_ingest_translates_traceparent_header(monkeypatch):
    _setup_env(monkeypatch)
    stub = _StubExecutionsClient()
    monkeypatch.setattr(api_ingest, "_wf_client", stub)

    uuid_values = iter(
        [
            uuid.UUID("00000000-0000-4000-8000-000000000200"),
            uuid.UUID("00000000-0000-4000-8000-000000000201"),
        ]
    )
    monkeypatch.setattr(api_ingest, "uuid4", lambda: next(uuid_values))

    client = TestClient(api_ingest.app)
    trace_parent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    expected_span = str(int("00f067aa0ba902b7", 16))
    headers = {"ce-id": "event-span", "ce-traceparent": trace_parent}
    payload = {"data": {"bucket": "trace-bucket", "name": "item.pdf"}}

    resp = client.post("/ingest", headers=headers, json=payload)
    assert resp.status_code == 200

    execution_argument = json.loads(stub.requests[0]["request"]["execution"]["argument"])
    assert execution_argument["trace_context"] == f"4bf92f3577b34da6a3ce929d0e0e4736/{expected_span};o=1"
    assert execution_argument["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"


def test_ingest_passes_pipeline_service_base_url(monkeypatch):
    _setup_env(monkeypatch)
    monkeypatch.setenv("PIPELINE_SERVICE_BASE_URL", "https://pipeline.test")
    stub = _StubExecutionsClient()
    monkeypatch.setattr(api_ingest, "_wf_client", stub)

    uuid_values = iter(
        [
            uuid.UUID("00000000-0000-4000-8000-000000000500"),
            uuid.UUID("00000000-0000-4000-8000-000000000501"),
        ]
    )
    monkeypatch.setattr(api_ingest, "uuid4", lambda: next(uuid_values))

    client = TestClient(api_ingest.app)
    payload = {"data": {"bucket": "base-bucket", "name": "payload.pdf"}}
    resp = client.post("/ingest", headers={"ce-id": "event-base"}, json=payload)
    assert resp.status_code == 200

    execution_argument = json.loads(stub.requests[0]["request"]["execution"]["argument"])
    assert execution_argument["pipeline_service_base_url"] == "https://pipeline.test"


def test_ingest_extracts_bucket_from_message_attributes(monkeypatch):
    _setup_env(monkeypatch)
    stub = _StubExecutionsClient()
    monkeypatch.setattr(api_ingest, "_wf_client", stub)

    uuid_values = iter(
        [
            uuid.UUID("00000000-0000-4000-8000-000000000300"),
            uuid.UUID("00000000-0000-4000-8000-000000000301"),
        ]
    )
    monkeypatch.setattr(api_ingest, "uuid4", lambda: next(uuid_values))

    client = TestClient(api_ingest.app)
    payload = {
        "message": {
            "attributes": {"bucketId": "attr-bucket", "objectId": "nested/path/report.pdf"},
        }
    }

    resp = client.post("/ingest", headers={"ce-id": "event-attr"}, json=payload)
    assert resp.status_code == 200

    execution_argument = json.loads(stub.requests[0]["request"]["execution"]["argument"])
    assert execution_argument["object_uri"] == "gs://attr-bucket/nested/path/report.pdf"
    assert execution_argument["intake_bucket"] == "attr-bucket"
    assert execution_argument["gcs_uri"] == "gs://attr-bucket/nested/path/report.pdf"


def test_ingest_extracts_bucket_from_base64_message(monkeypatch):
    _setup_env(monkeypatch)
    stub = _StubExecutionsClient()
    monkeypatch.setattr(api_ingest, "_wf_client", stub)

    uuid_values = iter(
        [
            uuid.UUID("00000000-0000-4000-8000-000000000400"),
            uuid.UUID("00000000-0000-4000-8000-000000000401"),
        ]
    )
    monkeypatch.setattr(api_ingest, "uuid4", lambda: next(uuid_values))

    client = TestClient(api_ingest.app)
    inner = json.dumps({"bucket": "encoded-bucket", "name": "from-data.pdf"}).encode("utf-8")
    payload = {
        "message": {
            "data": base64.b64encode(inner).decode("ascii"),
        }
    }

    resp = client.post("/ingest", headers={"ce-id": "event-data"}, json=payload)
    assert resp.status_code == 200

    execution_argument = json.loads(stub.requests[0]["request"]["execution"]["argument"])
    assert execution_argument["object_uri"] == "gs://encoded-bucket/from-data.pdf"
    assert execution_argument["intake_bucket"] == "encoded-bucket"
    assert execution_argument["gcs_uri"] == "gs://encoded-bucket/from-data.pdf"


def test_ingest_accepts_cloudevent_payload(monkeypatch):
    _setup_env(monkeypatch)
    stub = _StubExecutionsClient()
    monkeypatch.setattr(api_ingest, "_wf_client", stub)

    uuid_values = iter(
        [
            uuid.UUID("00000000-0000-4000-8000-000000000600"),
            uuid.UUID("00000000-0000-4000-8000-000000000601"),
        ]
    )
    monkeypatch.setattr(api_ingest, "uuid4", lambda: next(uuid_values))

    class _CloudEventStub:
        def __init__(self, event_dict: Dict[str, Any]) -> None:
            self._event_dict = event_dict

        def to_dict(self) -> Dict[str, Any]:
            return self._event_dict

    event_data = {"bucket": "cloudevent-bucket", "name": "source.pdf"}
    ce_dict = {
        "id": "ce-event-1",
        "type": "google.cloud.storage.object.v1.finalized",
        "source": "//storage.googleapis.com/projects/_/buckets/cloudevent-bucket",
        "specversion": "1.0",
        "data": event_data,
    }

    monkeypatch.setattr(api_ingest, "ce_from_http", lambda headers, body: _CloudEventStub(ce_dict))

    client = TestClient(api_ingest.app)
    headers = {
        "ce-id": "ce-event-1",
        "ce-type": ce_dict["type"],
        "ce-source": ce_dict["source"],
        "ce-specversion": ce_dict["specversion"],
        "Content-Type": "application/json",
    }

    resp = client.post("/ingest", data=json.dumps(event_data), headers=headers)
    assert resp.status_code == 200

    execution_argument = json.loads(stub.requests[0]["request"]["execution"]["argument"])
    assert execution_argument["object_uri"] == "gs://cloudevent-bucket/source.pdf"
    assert execution_argument["request_id"] == "ce-event-1"
    assert execution_argument["trace_id"] == "ce-event-1"


def test_ingest_returns_422_for_invalid_payload(monkeypatch):
    _setup_env(monkeypatch)
    stub = _StubExecutionsClient()
    monkeypatch.setattr(api_ingest, "_wf_client", stub)

    client = TestClient(api_ingest.app)
    resp = client.post("/ingest", data="not-json", headers={"Content-Type": "text/plain"})
    assert resp.status_code == 422
    assert stub.requests == []
