from __future__ import annotations

import json
from types import SimpleNamespace

from src.utils import pipeline_failures as pf


class _StubFuture:
    def __init__(self) -> None:
        self.waited = False

    def result(self, timeout: int | None = None) -> None:
        self.waited = True


def test_publish_failure_routes_by_stage(monkeypatch):
    published: dict[str, str] = {}

    class _StubPublisher:
        def publish(self, topic, data, **attributes):
            published["topic"] = topic
            published["data"] = data
            published["attrs"] = attributes
            return _StubFuture()

    cfg = SimpleNamespace(
        ocr_dlq_topic="projects/test/topics/ocr-dlq",
        summary_dlq_topic="projects/test/topics/summary-dlq",
        storage_dlq_topic="projects/test/topics/storage-dlq",
    )
    monkeypatch.setattr(pf, "get_config", lambda: cfg)
    monkeypatch.setattr(pf, "_get_publisher", lambda: _StubPublisher())

    assert pf.publish_pipeline_failure(
        stage="DOC_AI_SPLITTER",
        job_id="job-1",
        trace_id="trace-1",
        error=RuntimeError("boom"),
        metadata={"foo": "bar"},
    )
    assert published["topic"] == "projects/test/topics/ocr-dlq"
    payload = json.loads(published["data"])
    assert payload["job_id"] == "job-1"
    assert payload["trace_id"] == "trace-1"
    assert payload["metadata"]["foo"] == "bar"
    assert published["attrs"]["stage"] == "DOC_AI_SPLITTER"


def test_publish_failure_missing_topic(monkeypatch, caplog):
    cfg = SimpleNamespace(
        ocr_dlq_topic=None,
        summary_dlq_topic=None,
        storage_dlq_topic=None,
    )
    monkeypatch.setattr(pf, "get_config", lambda: cfg)
    caplog.set_level("WARNING")
    assert not pf.publish_pipeline_failure(stage="UNKNOWN_STAGE", error="fail")
    assert any("pipeline_failure_no_topic" in record.message for record in caplog.records)
