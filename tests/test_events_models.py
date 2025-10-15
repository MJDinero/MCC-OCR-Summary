from __future__ import annotations

from src.models.events import (
    DocumentIngestionEvent,
    OCRChunkMessage,
    StorageRequestMessage,
    SummaryRequestMessage,
    SummaryResultMessage,
)


def test_document_ingestion_event_round_trip() -> None:
    event = DocumentIngestionEvent(
        job_id="job-1",
        bucket="source-bucket",
        object_name="docs/report.pdf",
        generation="123456789",
        trace_id="trace-abc",
        request_id="req-1",
        gcs_uri="gs://source-bucket/docs/report.pdf",
        object_size=1024,
        sha256="abcd",
        attributes={"k": "v"},
    )
    data, attrs = event.to_pubsub()
    restored = DocumentIngestionEvent.from_pubsub(data, attrs)
    assert restored.job_id == event.job_id
    assert restored.bucket == event.bucket
    assert restored.attributes == {"k": "v"}
    assert restored.gcs_uri.endswith("report.pdf")


def test_ocr_chunk_message_round_trip() -> None:
    message = OCRChunkMessage(
        job_id="job-2",
        chunk_id="chunk-1",
        trace_id="trace-2",
        page_range=(3, 5),
        text="Some OCR text",
        shard_id="shard-A",
        metadata={"role": "primary"},
    )
    data, attrs = message.to_pubsub()
    restored = OCRChunkMessage.from_pubsub(data, attrs)
    assert restored.page_range == (3, 5)
    assert restored.metadata == {"role": "primary"}
    assert restored.shard_id == "shard-A"


def test_summary_request_message_round_trip() -> None:
    request = SummaryRequestMessage(
        job_id="job-3",
        chunk_id="chunk-xyz",
        trace_id="trace-3",
        text="This is source text",
        section_index=1,
        total_sections=4,
        aggregate=True,
        metadata={"foo": "bar"},
        max_words=250,
        doc_type="consult",
    )
    data, attrs = request.to_pubsub()
    restored = SummaryRequestMessage.from_pubsub(data, attrs)
    assert restored.aggregate is True
    assert restored.metadata == {"foo": "bar"}
    assert restored.max_words == 250
    assert restored.doc_type == "consult"


def test_summary_result_message_round_trip() -> None:
    result = SummaryResultMessage(
        job_id="job-4",
        chunk_id="chunk-3",
        trace_id="trace-4",
        summary_text="Summarised text",
        section_index=0,
        total_sections=2,
        tokens_used=123,
        aggregate=False,
        metadata={"partial": "true"},
    )
    data, attrs = result.to_pubsub()
    restored = SummaryResultMessage.from_pubsub(data, attrs)
    assert restored.summary_text == "Summarised text"
    assert restored.tokens_used == 123
    assert restored.metadata == {"partial": "true"}


def test_storage_request_message_round_trip() -> None:
    summary = SummaryResultMessage(
        job_id="job-5",
        chunk_id="chunk-5",
        trace_id="trace-5",
        summary_text="Result text",
        section_index=1,
        total_sections=2,
        tokens_used=50,
        aggregate=False,
        metadata={"source": "origin"},
    )
    request = StorageRequestMessage(
        job_id="job-5",
        trace_id="trace-5",
        final_summary="Final output",
        per_chunk_summaries=[summary],
        object_uri="gs://bucket/out.pdf",
        metadata={"source_uri": "gs://bucket/in.pdf"},
    )
    data, attrs = request.to_pubsub()
    # Attributes may include metadata entries for downstream consumers.
    attrs["extra"] = "value"
    restored = StorageRequestMessage.from_pubsub(data, attrs)
    assert restored.final_summary == "Final output"
    assert len(restored.per_chunk_summaries) == 1
    assert restored.metadata["source_uri"] == "gs://bucket/in.pdf"
    assert restored.metadata["extra"] == "value"
