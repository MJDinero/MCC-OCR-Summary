"""Streaming-aware chunking utilities for OCR and PDF processing."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Iterable, Iterator, Sequence

try:  # pragma: no cover - optional dependency at test time
    from google.cloud import storage  # type: ignore
except Exception:  # pragma: no cover - allow unit tests without GCS libs
    storage = None  # type: ignore

try:  # pragma: no cover - optional dependency
    from PyPDF2 import PdfReader, PdfWriter  # type: ignore
except Exception:  # pragma: no cover - allow offline tests to inject stubs
    PdfReader = None  # type: ignore
    PdfWriter = None  # type: ignore

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def estimate_token_count(text: str) -> int:
    """Return a coarse token count for the supplied text."""
    if not text:
        return 0
    return max(1, int(len(text) / 4))  # ≈4 characters per token


@dataclass(slots=True)
class Chunk:
    """Chunk of OCR text with provenance metadata."""

    text: str
    index: int
    page_start: int
    page_end: int
    token_count: int


class Chunker:
    """Incrementally chunk OCR output without loading the entire document."""

    def __init__(
        self,
        *,
        max_tokens: int = 4000,
        min_tokens: int | None = None,
        overlap_tokens: int = 0,
    ) -> None:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        self.max_tokens = max_tokens
        self.min_tokens = min_tokens or max_tokens // 2
        self.overlap_tokens = max(0, overlap_tokens)

    async def chunk_async(self, pages: AsyncIterator[str]) -> AsyncIterator[Chunk]:
        index = 0
        buffer: list[tuple[str, int]] = []
        buffer_tokens = 0

        async for page_number, page_text in _enumerate_async(pages, start=1):
            segments = _split_segments(page_text)
            for segment in segments:
                tokens = estimate_token_count(segment)
                if tokens >= self.max_tokens:
                    if buffer and buffer_tokens >= self.min_tokens:
                        (
                            chunk_text,
                            chunk_tokens,
                            chunk_start,
                            chunk_end,
                        ) = _flush_buffer(buffer, self.overlap_tokens)
                        yield Chunk(
                            text=chunk_text,
                            index=index,
                            page_start=chunk_start,
                            page_end=chunk_end,
                            token_count=chunk_tokens,
                        )
                        index += 1
                        buffer_tokens = sum(estimate_token_count(seg) for seg, _ in buffer)

                    yield Chunk(
                        text=segment.strip(),
                        index=index,
                        page_start=page_number,
                        page_end=page_number,
                        token_count=tokens,
                    )
                    index += 1
                    continue

                if buffer_tokens + tokens > self.max_tokens and buffer_tokens >= self.min_tokens:
                    (
                        chunk_text,
                        chunk_tokens,
                        chunk_start,
                        chunk_end,
                    ) = _flush_buffer(buffer, self.overlap_tokens)
                    yield Chunk(
                        text=chunk_text,
                        index=index,
                        page_start=chunk_start,
                        page_end=chunk_end,
                        token_count=chunk_tokens,
                    )
                    index += 1
                    buffer_tokens = sum(estimate_token_count(seg) for seg, _ in buffer)

                buffer.append((segment, page_number))
                buffer_tokens += tokens

        if buffer:
            chunk_text, chunk_tokens, chunk_start, chunk_end = _flush_buffer(buffer, 0)
            if chunk_text:
                yield Chunk(
                    text=chunk_text,
                    index=index,
                    page_start=chunk_start,
                    page_end=chunk_end,
                    token_count=chunk_tokens,
                )

    def chunk_sync(self, pages: Iterable[str]) -> Iterator[Chunk]:
        async def iterator() -> AsyncIterator[str]:
            for page in pages:
                yield page

        return _sync_iter(self.chunk_async(iterator()))


@dataclass(slots=True)
class PDFChunkArtifact:
    """Metadata describing a persisted PDF chunk."""

    uri: str
    index: int
    page_start: int
    page_end: int
    size_bytes: int
    created_at: str
    expires_at: str
    sha256: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class PDFChunkManifest:
    """Manifest emitted after PDF chunking completes."""

    source_uri: str
    job_id: str
    created_at: str
    expires_at: str
    manifest_uri: str
    artifacts: list[PDFChunkArtifact]


class PDFChunker:
    """Split PDFs stored in GCS into <=25 page artifacts with retention metadata."""

    def __init__(
        self,
        *,
        storage_client: object | None = None,
        max_pages: int = 25,
        retention_days: int = 7,
        artifact_bucket: str | None = None,
        artifact_prefix: str = "artifacts/pdf-chunks",
        tmp_dir: str | None = None,
    ) -> None:
        if PdfReader is None or PdfWriter is None:  # pragma: no cover - dependency validated in tests
            raise RuntimeError("PyPDF2 must be installed to use PDFChunker")
        if max_pages <= 0:
            raise ValueError("max_pages must be positive")
        self.max_pages = max_pages
        self.retention_days = max(1, retention_days)
        self._storage = storage_client or _build_storage_client()
        self._artifact_bucket = artifact_bucket
        self._artifact_prefix = artifact_prefix.strip("/")
        self._tmp_dir = tmp_dir
        self._last_manifest: PDFChunkManifest | None = None

    @property
    def last_manifest(self) -> PDFChunkManifest | None:
        """Return the manifest produced by the most recent `chunk_pdf` call."""

        return self._last_manifest

    def chunk_pdf(
        self,
        source_uri: str,
        *,
        job_id: str,
        destination_prefix: str | None = None,
        metadata: dict[str, str] | None = None,
        destination_bucket: str | None = None,
    ) -> Iterator[PDFChunkArtifact]:
        """Stream PDF chunks to GCS, yielding metadata for each uploaded artifact."""

        if not job_id:
            raise ValueError("job_id is required to produce deterministic artifact paths")
        bucket_name, object_name = _parse_gcs_uri(source_uri)
        dest_bucket_name = destination_bucket or self._artifact_bucket or bucket_name
        dest_prefix = destination_prefix or f"{self._artifact_prefix}/{job_id}"
        dest_prefix = dest_prefix.strip("/")
        created_at = _isoformat(datetime.now(timezone.utc))
        expires_at = _isoformat(datetime.now(timezone.utc) + timedelta(days=self.retention_days))
        base_metadata = dict(metadata or {})
        source_bucket = self._storage.bucket(bucket_name)
        source_blob = source_bucket.blob(object_name)
        dest_bucket = self._storage.bucket(dest_bucket_name)
        artifacts: list[PDFChunkArtifact] = []

        with self._temp_path(suffix=".pdf") as temp_pdf:
            source_blob.download_to_filename(temp_pdf)
            reader = PdfReader(temp_pdf)
            total_pages = len(reader.pages)
            if total_pages == 0:
                raise ValueError("PDF contains no pages; cannot chunk an empty document")
            for index, start in enumerate(range(0, total_pages, self.max_pages)):
                page_start = start + 1
                page_end = min(total_pages, start + self.max_pages)
                writer = PdfWriter()
                for page_idx in range(start, page_end):
                    writer.add_page(reader.pages[page_idx])
                with self._temp_path(suffix=".pdf") as chunk_path:
                    with open(chunk_path, "wb") as fh:
                        writer.write(fh)
                    chunk_name = _compose_blob_name(dest_prefix, f"chunk-{index:04d}.pdf")
                    chunk_blob = dest_bucket.blob(chunk_name)
                    chunk_metadata = {
                        **base_metadata,
                        "job_id": job_id,
                        "source_uri": source_uri,
                        "retain_until": expires_at,
                        "page_start": str(page_start),
                        "page_end": str(page_end),
                        "chunk_index": str(index),
                    }
                    chunk_blob.metadata = chunk_metadata
                    chunk_blob.cache_control = "no-store"
                    chunk_blob.upload_from_filename(chunk_path, content_type="application/pdf")
                    size_bytes = os.path.getsize(chunk_path)
                    sha256 = _sha256_file(chunk_path)
                    artifact = PDFChunkArtifact(
                        uri=f"gs://{dest_bucket_name}/{chunk_name}",
                        index=index,
                        page_start=page_start,
                        page_end=page_end,
                        size_bytes=size_bytes,
                        created_at=created_at,
                        expires_at=expires_at,
                        sha256=sha256,
                        metadata=chunk_metadata,
                    )
                    artifacts.append(artifact)
                    yield artifact

        manifest_uri = self._write_manifest(
            dest_bucket=dest_bucket,
            dest_prefix=dest_prefix,
            source_uri=source_uri,
            job_id=job_id,
            created_at=created_at,
            expires_at=expires_at,
            artifacts=artifacts,
            metadata=base_metadata,
        )
        self._last_manifest = PDFChunkManifest(
            source_uri=source_uri,
            job_id=job_id,
            created_at=created_at,
            expires_at=expires_at,
            manifest_uri=manifest_uri,
            artifacts=artifacts,
        )

    def cleanup_expired_artifacts(
        self,
        *,
        bucket: str | None = None,
        prefix: str | None = None,
        now: datetime | None = None,
    ) -> list[str]:
        """Delete chunk artifacts whose retention window has elapsed."""

        bucket_name = bucket or self._artifact_bucket
        if not bucket_name:
            raise ValueError("bucket must be provided when artifact_bucket is unset")
        search_prefix = (prefix or self._artifact_prefix).strip("/") + "/"
        horizon = now or datetime.now(timezone.utc)
        deleted: list[str] = []
        for blob in self._storage.list_blobs(bucket_name, prefix=search_prefix):
            metadata = getattr(blob, "metadata", None) or {}
            retain_until = metadata.get("retain_until")
            if not retain_until:
                continue
            try:
                expires_at = datetime.fromisoformat(retain_until.replace("Z", "+00:00"))
            except ValueError:
                continue
            if expires_at <= horizon:
                blob.delete()
                name = getattr(blob, "name", "")
                if name:
                    deleted.append(name)
        return deleted

    def _write_manifest(
        self,
        *,
        dest_bucket: object,
        dest_prefix: str,
        source_uri: str,
        job_id: str,
        created_at: str,
        expires_at: str,
        artifacts: list[PDFChunkArtifact],
        metadata: dict[str, str],
    ) -> str:
        manifest_payload = {
            "source_uri": source_uri,
            "job_id": job_id,
            "created_at": created_at,
            "expires_at": expires_at,
            "retention_days": self.retention_days,
            "max_pages_per_chunk": self.max_pages,
            "artifacts": [
                {
                    "uri": artifact.uri,
                    "index": artifact.index,
                    "page_start": artifact.page_start,
                    "page_end": artifact.page_end,
                    "size_bytes": artifact.size_bytes,
                    "sha256": artifact.sha256,
                }
                for artifact in artifacts
            ],
        }
        manifest_name = _compose_blob_name(dest_prefix, "manifest.json")
        manifest_blob = dest_bucket.blob(manifest_name)
        manifest_blob.metadata = {
            **metadata,
            "job_id": job_id,
            "retain_until": expires_at,
            "source_uri": source_uri,
        }
        manifest_blob.cache_control = "no-store"
        manifest_blob.upload_from_string(
            json.dumps(manifest_payload, separators=(",", ":")),
            content_type="application/json",
        )
        return f"gs://{dest_bucket.name}/{manifest_name}"

    @contextlib.contextmanager
    def _temp_path(self, *, suffix: str) -> Iterator[str]:
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, dir=self._tmp_dir, delete=False)
        try:
            tmp.close()
            yield tmp.name
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.remove(tmp.name)


def _compose_blob_name(prefix: str, name: str) -> str:
    prefix = prefix.strip("/")
    if not prefix:
        return name
    return f"{prefix}/{name}"


def _build_storage_client() -> object:
    if storage is None:  # pragma: no cover - exercised in integration environments
        raise RuntimeError(
            "google-cloud-storage is required for PDF chunking when no storage_client is provided",
        )
    return storage.Client()


def _parse_gcs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"Expected gs:// URI, received {uri}")
    without = uri[5:]
    bucket, _, path = without.partition("/")
    if not bucket or not path:
        raise ValueError(f"Invalid GCS URI: {uri}")
    return bucket, path


def _isoformat(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def _enumerate_async(iterator: AsyncIterator[str], start: int = 0) -> AsyncIterator[tuple[int, str]]:
    index = start
    async for item in iterator:
        yield index, item
        index += 1


def _split_segments(text: str) -> Sequence[str]:
    stripped = text.strip()
    if not stripped:
        return ()
    parts = SENTENCE_SPLIT_RE.split(stripped)
    return tuple(part.strip() for part in parts if part.strip())


def _flush_buffer(buffer: list[tuple[str, int]], overlap_tokens: int) -> tuple[str, int, int, int]:
    chunk_text = " ".join(segment for segment, _ in buffer).strip()
    chunk_tokens = sum(estimate_token_count(segment) for segment, _ in buffer)
    if not chunk_text:
        buffer.clear()
        return "", 0, 1, 1
    pages = [page for _, page in buffer]
    page_start = min(pages)
    page_end = max(pages)
    if overlap_tokens <= 0:
        buffer.clear()
        return chunk_text, chunk_tokens, page_start, page_end

    overlap: list[tuple[str, int]] = []
    overlap_total = 0
    for segment, page in reversed(buffer):
        overlap_total += estimate_token_count(segment)
        overlap.append((segment, page))
        if overlap_total >= overlap_tokens:
            break
    buffer[:] = list(reversed(overlap))
    return chunk_text, chunk_tokens, page_start, page_end


def _sync_iter(iterator: AsyncIterator[Chunk]) -> Iterator[Chunk]:
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)

        async def consume() -> list[Chunk]:
            result: list[Chunk] = []
            async for item in iterator:
                result.append(item)
            return result

        return iter(loop.run_until_complete(consume()))
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            loop.close()
            asyncio.set_event_loop(None)


__all__ = [
    "Chunker",
    "Chunk",
    "estimate_token_count",
    "PDFChunker",
    "PDFChunkArtifact",
    "PDFChunkManifest",
]
