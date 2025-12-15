"""Typed Pub/Sub messages shared across MCC OCR Summary services."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Mapping

ISO8601 = "%Y-%m-%dT%H:%M:%S.%fZ"


def _now_utc() -> str:
    return datetime.now(tz=timezone.utc).strftime(ISO8601)


def _ensure_str_dict(values: Mapping[str, Any] | None) -> dict[str, str]:
    if not values:
        return {}
    return {str(key): str(value) for key, value in values.items()}


def _decode_pubsub_data(data: bytes | str) -> dict[str, Any]:
    if isinstance(data, bytes):
        raw = data.decode("utf-8")
    else:
        raw = data
    return json.loads(raw)


def _encode_pubsub_data(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


@dataclass(slots=True)
class DocumentIngestionEvent:
    """Message emitted by intake when a PDF is ready for OCR processing."""

    job_id: str
    bucket: str
    object_name: str
    generation: str
    trace_id: str
    request_id: str | None = None
    gcs_uri: str | None = None
    object_size: int | None = None
    sha256: str | None = None
    created_at: str = field(default_factory=_now_utc)
    attributes: dict[str, str] = field(default_factory=dict)

    def to_pubsub(self) -> tuple[bytes, dict[str, str]]:
        payload = asdict(self)
        # Attributes stored separately, omit from payload to avoid duplication
        attributes = payload.pop("attributes", {})
        payload.setdefault("event_type", "document.ingested")
        payload.setdefault(
            "gcs_uri", self.gcs_uri or f"gs://{self.bucket}/{self.object_name}"
        )
        payload.setdefault("message_id", uuid.uuid4().hex)
        payload.setdefault("created_at", self.created_at)
        encoded = _encode_pubsub_data(payload)
        return encoded, attributes

    @classmethod
    def from_pubsub(
        cls,
        data: bytes | str,
        attributes: Mapping[str, str] | None = None,
    ) -> "DocumentIngestionEvent":
        payload = _decode_pubsub_data(data)
        payload.pop("event_type", None)
        payload.pop("message_id", None)
        payload.setdefault("attributes", _ensure_str_dict(attributes))
        return cls(**payload)


@dataclass(slots=True)
class OCRChunkMessage:
    """Chunk emitted by the OCR service for downstream summarisation."""

    job_id: str
    chunk_id: str
    trace_id: str
    page_range: tuple[int, int]
    text: str
    shard_id: str | None = None
    source_event_id: str | None = None
    total_pages: int | None = None
    created_at: str = field(default_factory=_now_utc)
    metadata: dict[str, str] = field(default_factory=dict)

    def to_pubsub(self) -> tuple[bytes, dict[str, str]]:
        payload = asdict(self)
        metadata = payload.pop("metadata", {})
        payload.setdefault("event_type", "ocr.chunk.ready")
        payload.setdefault("created_at", self.created_at)
        if "page_range" in payload:
            payload["page_range"] = list(self.page_range)
        encoded = _encode_pubsub_data(payload)
        return encoded, _ensure_str_dict(metadata)

    @classmethod
    def from_pubsub(
        cls,
        data: bytes | str,
        attributes: Mapping[str, str] | None = None,
    ) -> "OCRChunkMessage":
        payload = _decode_pubsub_data(data)
        payload.setdefault("metadata", _ensure_str_dict(attributes))
        payload.pop("event_type", None)
        if "page_range" in payload and isinstance(payload["page_range"], list):
            start, end = payload["page_range"]
            payload["page_range"] = (int(start), int(end))
        return cls(**payload)


@dataclass(slots=True)
class SummaryRequestMessage:
    """Message consumed by summarisation service to build hierarchical summaries."""

    job_id: str
    chunk_id: str
    trace_id: str
    text: str
    section_index: int
    total_sections: int
    aggregate: bool = False
    metadata: dict[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_utc)
    max_words: int | None = None
    doc_type: str | None = None

    def to_pubsub(self) -> tuple[bytes, dict[str, str]]:
        payload = asdict(self)
        metadata = payload.pop("metadata", {})
        payload.setdefault("event_type", "summary.chunk.requested")
        payload.setdefault("created_at", self.created_at)
        encoded = _encode_pubsub_data(payload)
        return encoded, _ensure_str_dict(metadata)

    @classmethod
    def from_pubsub(
        cls,
        data: bytes | str,
        attributes: Mapping[str, str] | None = None,
    ) -> "SummaryRequestMessage":
        payload = _decode_pubsub_data(data)
        payload.setdefault("metadata", _ensure_str_dict(attributes))
        payload.pop("event_type", None)
        return cls(**payload)


@dataclass(slots=True)
class SummaryResultMessage:
    """Summarisation result emitted for storage (and potential aggregation)."""

    job_id: str
    chunk_id: str
    trace_id: str
    summary_text: str
    section_index: int
    total_sections: int
    tokens_used: int | None = None
    aggregate: bool = False
    created_at: str = field(default_factory=_now_utc)
    metadata: dict[str, str] = field(default_factory=dict)

    def to_pubsub(self) -> tuple[bytes, dict[str, str]]:
        payload = asdict(self)
        metadata = payload.pop("metadata", {})
        payload.setdefault("event_type", "summary.result.ready")
        payload.setdefault("created_at", self.created_at)
        encoded = _encode_pubsub_data(payload)
        return encoded, _ensure_str_dict(metadata)

    @classmethod
    def from_pubsub(
        cls,
        data: bytes | str,
        attributes: Mapping[str, str] | None = None,
    ) -> "SummaryResultMessage":
        payload = _decode_pubsub_data(data)
        payload.setdefault("metadata", _ensure_str_dict(attributes))
        payload.pop("event_type", None)
        return cls(**payload)


@dataclass(slots=True)
class StorageRequestMessage:
    """Final message instructing storage service to persist the summary output."""

    job_id: str
    trace_id: str
    final_summary: dict[str, Any]
    per_chunk_summaries: list[SummaryResultMessage]
    object_uri: str
    metadata: dict[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_utc)

    def to_pubsub(self) -> tuple[bytes, dict[str, str]]:
        payload = asdict(self)
        metadata = payload.pop("metadata", {})
        payload["per_chunk_summaries"] = [
            asdict(item) for item in self.per_chunk_summaries
        ]
        payload.setdefault("event_type", "storage.persist.requested")
        payload.setdefault("created_at", self.created_at)
        encoded = _encode_pubsub_data(payload)
        return encoded, _ensure_str_dict(metadata)

    @classmethod
    def from_pubsub(
        cls,
        data: bytes | str,
        attributes: Mapping[str, str] | None = None,
    ) -> "StorageRequestMessage":
        payload = _decode_pubsub_data(data)
        summaries = payload.get("per_chunk_summaries", [])
        payload["per_chunk_summaries"] = [
            SummaryResultMessage(**summary) for summary in summaries
        ]
        payload.pop("event_type", None)
        payload.setdefault("metadata", _ensure_str_dict(attributes))
        return cls(**payload)


__all__ = [
    "DocumentIngestionEvent",
    "OCRChunkMessage",
    "SummaryRequestMessage",
    "SummaryResultMessage",
    "StorageRequestMessage",
]
