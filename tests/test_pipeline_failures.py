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


def test_resolve_topic_matches_variants():
    cfg = SimpleNamespace(
        ocr_dlq_topic="ocr",
        summary_dlq_topic="summary",
        storage_dlq_topic="storage",
    )
    assert pf._resolve_topic("doc_ai_splitter", cfg) == "ocr"  # type: ignore[attr-defined]
    assert pf._resolve_topic("supervisor", cfg) == "summary"  # type: ignore[attr-defined]
    assert pf._resolve_topic("pdf_writer", cfg) == "storage"  # type: ignore[attr-defined]
    assert pf._resolve_topic(None, cfg) is None  # type: ignore[attr-defined]


def test_publish_failure_records_metrics(monkeypatch, caplog):
    cfg = SimpleNamespace(
        ocr_dlq_topic=None,
        summary_dlq_topic=None,
        storage_dlq_topic=None,
    )
    monkeypatch.setattr(pf, "get_config", lambda: cfg)

    class _StubCounter:
        def __init__(self) -> None:
            self.labels_called: list[dict[str, str]] = []

        def labels(self, **labels: str) -> "_StubCounter":
            self.labels_called.append(labels)
            return self

        def inc(self) -> None:
            self.labels_called[-1]["inc"] = "1"

    stub = _StubCounter()
    monkeypatch.setattr(pf, "_PIPELINE_FAILURES", stub)
    caplog.set_level("WARNING")
    assert not pf.publish_pipeline_failure(stage="OCR")
    assert stub.labels_called
    assert stub.labels_called[-1]["stage"] == "OCR"
