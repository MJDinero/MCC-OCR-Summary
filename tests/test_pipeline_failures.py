import json
from types import SimpleNamespace

from src.utils import pipeline_failures


class _StubPublisher:
    def __init__(self):
        self.calls = []

    def publish(self, topic, data, **attributes):
        self.calls.append((topic, data, attributes))
        return SimpleNamespace(result=lambda: None)


def test_publish_pipeline_failure_sends_message(monkeypatch):
    monkeypatch.setenv("PIPELINE_DLQ_TOPIC", "projects/proj/topics/dlq")
    stub = _StubPublisher()
    monkeypatch.setattr(pipeline_failures, "_PUBLISHER", stub)
    pipeline_failures.publish_pipeline_failure(
        stage="TEST_STAGE",
        job_id="job-123",
        trace_id="trace-xyz",
        error=ValueError("boom"),
        metadata={"foo": "bar"},
    )
    assert stub.calls, "DLQ publish should have been invoked"
    topic, data, attrs = stub.calls[0]
    assert topic == "projects/proj/topics/dlq"
    payload = json.loads(data.decode("utf-8"))
    assert payload["metadata"]["foo"] == "bar"
    assert attrs["job_id"] == "job-123"
    assert attrs["stage"] == "TEST_STAGE"


def test_publish_pipeline_failure_noop_without_job(monkeypatch):
    monkeypatch.setenv("PIPELINE_DLQ_TOPIC", "projects/proj/topics/dlq")
    stub = _StubPublisher()
    monkeypatch.setattr(pipeline_failures, "_PUBLISHER", stub)
    pipeline_failures.publish_pipeline_failure(stage="TEST_STAGE", job_id=None)
    assert stub.calls == []
