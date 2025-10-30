"""Document AI OCR service abstraction with retry & dependency injection.

This module exposes an `OCRService` that can be injected into the API layer or
other services. The implementation wraps Google Document AI but shields callers
from library specific concerns (request construction, transient retries, result
normalisation) and converts errors into internal domain exceptions.
"""

from __future__ import annotations

import io
import json
import logging
import random
import re
import tempfile
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional, Protocol, Sequence, cast

from tenacity import (
    retry,
    wait_exponential,
    stop_after_attempt,
    retry_if_exception_type,
)
from google.api_core.client_options import ClientOptions
from google.api_core import exceptions as gexc
from google.cloud import documentai_v1 as documentai
from google.protobuf.json_format import MessageToDict

from src.config import get_config, AppConfig
from src.utils.docai_request_builder import build_docai_request
from src.errors import OCRServiceError, ValidationError
from src.services.docai_batch_helper import batch_process_documents_gcs
from src.services.pipeline import (
    PipelineStatus,
    PipelineStateStore,
    create_state_store_from_env,
)
from src.utils.pipeline_failures import publish_pipeline_failure

try:  # pragma: no cover - optional dependency fallback
    from pypdf import PdfReader, PdfWriter  # type: ignore
except Exception:  # pragma: no cover - allow runtime environments without pypdf
    PdfReader = None  # type: ignore
    PdfWriter = None  # type: ignore

_LOG = logging.getLogger("ocr_service")
DEFAULT_CHUNK_MAX_PAGES = 25
FALLBACK_CHUNK_MAX_PAGES = 20
MIN_CHUNK_MAX_PAGES = 15
MAX_CHUNK_CONCURRENCY = 2
MAX_CHUNK_RETRIES = 5
_RETRYABLE_STATUS_NAMES = {
    "STATUSCODE.RESOURCE_EXHAUSTED",
    "STATUSCODE.UNAVAILABLE",
    "STATUSCODE.DEADLINE_EXCEEDED",
    "STATUSCODE.ABORTED",
    "STATUSCODE.INTERNAL",
}
_RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}

_NOISE_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?im)^\s*(?:fax|facsimile)\s*[:\-].*$"),
    re.compile(r"(?im)^\s*(?:to|from)\s*[:\-].*$"),
    re.compile(r"(?im)\bpage(?:\s+|:\s*)\d+(?:\s+of\s+\d+)?\b.*$"),
    re.compile(r"(?im)\b(cpt|icd[- ]?\d*)\b.*$"),
    re.compile(r"(?im)\bprocedure\s+code[: ]?\w+.*$"),
    re.compile(
        r"(?im)\b(billed|billing|charges?|ledger|invoice|statement\s+covers\s+period|amount\s+due)\b.*$"
    ),
    re.compile(
        r"(?im)\b(payer|health\s+plan\s+id|group\s+no\.?|claim\s+no\.?|account\s+no\.?)\b.*$"
    ),
    re.compile(r"(?im)\b(timestamps?|generated on|scanned on)\b.*$"),
    re.compile(r"(?im)\b(npi|mrn|acct#?)\b.*$"),
    re.compile(
        r"(?im)\b(affidavit|notary|custodian|commission\s+expires|state\s+of|county\s+of|sworn|regular\s+course\s+of\s+business|original\s+or\s+duplicate)\b.*$"
    ),
    re.compile(r"(?im)\b(true\s+and\s+correct\s+copy|attached\s+hereto)\b.*$"),
    re.compile(
        r"(?im)\b(follow\s+(?:the\s+)?instructions\s+from\s+your\s+(?:health\s*care|healthcare)\s+provider)\b.*$"
    ),
    re.compile(
        r"(?im)\b(seek\s+immediate\s+medical\s+attention|go\s+to\s+(?:the\s+)?nearest\s+emergency\s+department|call\s+911)\b.*$"
    ),
    re.compile(r"(?im)\b(do\s+not\s+(?:drive|operate\s+heavy\s+machinery))\b.*$"),
    re.compile(
        r"(?im)\b(signs?\s+of\s+infection(?:\s+(?:fever|chills|redness|swelling|warmth|drainage))?)\b.*$"
    ),
)
_NOISE_KEYWORD_PATTERN = re.compile(
    r"(?im)\b("
    r"affidavit|notary|custodian|commission\s+expires|state\s+of|county\s+of|sworn|true\s+and\s+correct\s+copy|attached\s+hereto|regular\s+course\s+of\s+business|original\s+or\s+duplicate"
    r"|ledger|invoice|charges?|amount\s+due|payer|health\s+plan\s+id|group\s+no\.?|claim\s+no\.?|account\s+no\.?"
    r"|follow\s+(?:the\s+)?instructions\s+from\s+your\s+(?:health\s*care|healthcare)\s+provider"
    r"|seek\s+immediate\s+medical\s+attention|nearest\s+emergency\s+department|call\s+911"
    r"|signs?\s+of\s+infection"
    r")\b"
)
_NOISE_TABLE_CHARS_RE = re.compile(r"[│║╚═╦╩╣╠┼┤├┐└┘┌─]+")


def _strip_noise(raw_text: str) -> str:
    """Remove fax headers, billing/legal boilerplate, and table debris while retaining spacing."""

    if not raw_text:
        return ""
    lines: list[str] = []
    for raw_line in raw_text.splitlines():
        table_scrubbed = _NOISE_TABLE_CHARS_RE.sub(" ", raw_line)
        candidate = table_scrubbed.strip()
        if not candidate:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if any(pattern.search(candidate) for pattern in _NOISE_LINE_PATTERNS):
            continue
        if _NOISE_KEYWORD_PATTERN.search(candidate):
            continue
        lines.append(candidate)
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def clean_ocr_output(raw_text: str) -> str:
    """Strip headers, fax artifacts, billing codes, page marks, and table debris from OCR output."""

    if not raw_text:
        return ""
    cleaned = _strip_noise(raw_text)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


class _DocAIClientProtocol(Protocol):  # pragma: no cover - structural typing aid
    def process_document(self, request: Dict[str, Any]) -> Any:  # noqa: D401
        ...


def _default_client(endpoint: str) -> _DocAIClientProtocol:
    return documentai.DocumentProcessorServiceClient(
        client_options=ClientOptions(api_endpoint=endpoint)
    )


def _extract_document_dict(result: Any) -> Dict[str, Any]:
    """Extract a plain dict from a DocumentProcessorService response.

    Supports both real Document AI response objects and simplified mocks used
    in tests. We only rely on a subset: full text and per-page text.
    """
    if hasattr(result, "document"):
        doc = result.document
    else:
        doc = result

    # Real object path: documentai.Document (protobuf message)
    try:  # pragma: no cover - best effort
        if hasattr(doc, "_pb"):
            return MessageToDict(
                doc._pb, preserving_proto_field_name=True
            )  # pylint: disable=protected-access
    except (
        AttributeError,
        TypeError,
        ValueError,
    ):  # pragma: no cover - narrow expected issues
        pass

    if isinstance(doc, dict):
        return doc
    raise OCRServiceError("Unsupported Document AI response format")


def _normalise(doc: Dict[str, Any]) -> Dict[str, Any]:
    pages_out = []
    pages = doc.get("pages") or []
    for idx, p in enumerate(pages, start=1):
        text = p.get("layout", {}).get("text", "") if isinstance(p, dict) else ""
        if not text:
            # Some simplified fixtures may already provide 'text'
            text = p.get("text", "") if isinstance(p, dict) else ""
        pages_out.append({"page_number": idx, "text": _strip_noise(text)})
    full_text_source = doc.get("text") or " ".join(pg["text"] for pg in pages_out)
    full_text = _strip_noise(full_text_source)
    return {"text": full_text, "pages": pages_out}


def _split_pdf_bytes(
    pdf_bytes: bytes, *, max_pages: int = DEFAULT_CHUNK_MAX_PAGES
) -> list[bytes]:
    """Split PDF bytes into <= max_pages chunks."""
    if PdfReader is None or PdfWriter is None:
        raise OCRServiceError(
            "pypdf is required for PDF splitting but is not installed"
        )
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:
        raise OCRServiceError(f"Failed to read PDF for splitting: {exc}") from exc

    if max_pages <= 0:
        raise ValueError("max_pages must be positive")

    parts: list[bytes] = []
    total_pages = len(reader.pages)
    for start in range(0, total_pages, max_pages):
        writer = PdfWriter()
        for page in reader.pages[start : start + max_pages]:
            writer.add_page(page)
        buffer = io.BytesIO()
        writer.write(buffer)
        buffer.seek(0)
        parts.append(buffer.read())
    return parts


class _ChunkPageLimitExceeded(Exception):
    """Raised when DocAI reports PAGE_LIMIT_EXCEEDED for a chunk."""


def _is_page_limit_error(exc: Exception) -> bool:
    message = str(getattr(exc, "message", None) or exc)
    return "PAGE_LIMIT_EXCEEDED" in message or "page limit" in message.lower()


def _normalise_status_code(exc: Exception) -> tuple[str | None, int | None]:
    """Extract textual and numeric status codes from DocAI exceptions."""
    code = getattr(exc, "code", None)
    code_text = str(code).upper() if code is not None else None
    http_status: int | None = None
    if hasattr(exc, "response") and getattr(exc, "response", None) is not None:
        http_status = getattr(getattr(exc, "response", None), "status_code", None)
    if http_status is None and hasattr(exc, "status_code"):
        try:
            http_status = int(getattr(exc, "status_code"))
        except Exception:  # pragma: no cover - best effort
            http_status = None
    if http_status is None and hasattr(exc, "errors"):
        try:
            first_error = exc.errors[0]
            http_status = int(first_error.get("reason"))  # pragma: no cover - defensive
        except Exception:
            http_status = None
    return code_text, http_status


def _is_retryable_error(exc: Exception) -> bool:
    if isinstance(exc, (gexc.ServiceUnavailable, gexc.DeadlineExceeded, gexc.Aborted)):
        return True
    if isinstance(exc, gexc.ResourceExhausted):
        if _is_page_limit_error(exc):
            return False
        return True
    if isinstance(exc, gexc.GoogleAPICallError):
        code_text, http_status = _normalise_status_code(exc)
        if code_text and code_text in _RETRYABLE_STATUS_NAMES:
            return True
        if http_status and http_status in _RETRYABLE_HTTP_STATUSES:
            return True
        message = str(exc).lower()
        if any(
            token in message
            for token in ("429", "unavailable", "resource_exhausted", "internal")
        ):
            return True
    return False


def _call_docai_with_backoff(
    *,
    client: _DocAIClientProtocol,
    request: Dict[str, Any],
) -> Any:
    attempt = 1
    backoff = 1.0
    while True:
        try:
            return client.process_document(request=request)
        except Exception as exc:  # pylint: disable=broad-except
            if _is_page_limit_error(exc):
                raise _ChunkPageLimitExceeded(str(exc)) from exc
            should_retry = _is_retryable_error(exc)
            if not should_retry or attempt >= MAX_CHUNK_RETRIES:
                raise
            sleep_for = min(backoff * (2 ** (attempt - 1)), 16.0) + random.uniform(
                0.1, 0.6
            )
            _LOG.warning(
                "docai_chunk_retry",
                extra={
                    "attempt": attempt,
                    "sleep_seconds": round(sleep_for, 2),
                    "error": type(exc).__name__,
                },
            )
            time.sleep(sleep_for)
            attempt += 1


def _process_chunk_with_docai(
    *,
    client: _DocAIClientProtocol,
    pdf_bytes: bytes,
    project_id: str,
    location: str,
    processor_id: str,
    cfg: AppConfig,
) -> Dict[str, Any]:
    """Process PDF bytes through Document AI and return a normalised payload."""
    try:
        _name, request = build_docai_request(
            pdf_bytes,
            project_id,
            location,
            processor_id,
            legacy_layout=getattr(cfg, "doc_ai_legacy_layout", False),
            enable_image_quality_scores=getattr(
                cfg, "doc_ai_enable_image_quality_scores", True
            ),
        )
    except ValidationError:
        raise
    except Exception as exc:
        raise OCRServiceError(f"Failed building DocAI request: {exc}") from exc

    request.pop("encryption_spec", None)

    start = time.perf_counter()
    try:
        result = _call_docai_with_backoff(client=client, request=request)
    except _ChunkPageLimitExceeded:
        raise
    except gexc.GoogleAPICallError as exc:
        raise OCRServiceError(f"DocAI chunk failed: {exc}") from exc
    except Exception as exc:  # pragma: no cover - defensive catch
        raise OCRServiceError(f"Unexpected DocAI chunk error: {exc}") from exc
    finally:
        elapsed = time.perf_counter() - start
        _LOG.debug(
            "docai_chunk_attempt",
            extra={"elapsed_ms": round(elapsed * 1000, 2), "processor": processor_id},
        )

    raw_doc = _extract_document_dict(result)
    return _normalise(raw_doc)


@dataclass
class OCRService:
    """High level OCR service wrapper.

    Parameters:
        processor_id: Document AI processor ID (OCR layout or similar).
        config: Optional application configuration; if omitted global config used.
        client_factory: Callable returning a DocumentProcessorServiceClient (DI / testability).
        request_timeout: Per attempt timeout in seconds (not currently enforced at SDK level if SDK lacks param).
    """

    processor_id: str
    config: Optional[AppConfig] = None
    client_factory: Optional[Callable[[str], _DocAIClientProtocol]] = None
    request_timeout: float = 90.0

    def __post_init__(self) -> None:
        if not self.processor_id:
            raise ValueError("processor_id required")
        self._cfg = self.config or get_config()
        self._client_factory = self.client_factory or _default_client
        self._docai_location = getattr(self._cfg, "doc_ai_location", self._cfg.region)
        self._endpoint = f"{self._docai_location}-documentai.googleapis.com"
        self._client = self._client_factory(self._endpoint)
        self._kms_key = getattr(self._cfg, "cmek_key_name", None)

    def close(
        self,
    ) -> None:  # pragma: no cover - underlying client close may not be needed
        close_attr = getattr(self._client, "close", None)
        if not callable(close_attr):  # nothing to do
            return
        try:
            close_fn = cast(Callable[[], Any], close_attr)
            close_fn()  # pylint: disable=not-callable
        except (RuntimeError, OSError):  # swallow close errors
            _LOG.debug("Failed to close client", exc_info=True)

    # Retry on transient service availability / deadline conditions.
    @retry(
        wait=wait_exponential(multiplier=0.5, max=8),
        stop=stop_after_attempt(5),
        retry=retry_if_exception_type((gexc.ServiceUnavailable, gexc.DeadlineExceeded)),
        reraise=True,
    )
    def process(self, file_source: Any) -> Dict[str, Any]:
        """Run OCR on provided PDF path or bytes.

        Returns a normalised dictionary: { text: str, pages: [{page_number, text}, ...] }
        Raises OCRServiceError / ValidationError.
        """
        # Thresholds for switching to batch mode (asynchronous Document AI)
        PAGES_BATCH_THRESHOLD = 30
        SIZE_BATCH_THRESHOLD = 40 * 1024 * 1024  # 40MB

        project_id = self._cfg.project_id
        location = getattr(
            self,
            "_docai_location",
            getattr(self._cfg, "doc_ai_location", self._cfg.region),
        )

        # Pre-read bytes to estimate size & pages for batching decision.
        pdf_bytes: Optional[bytes] = None
        source_path: Optional[Path] = None
        if isinstance(file_source, (bytes, bytearray)):
            pdf_bytes = bytes(file_source)
        elif isinstance(file_source, (str, Path)):
            source_path = Path(file_source)
            if not source_path.exists():
                raise ValidationError(f"File not found: {source_path}")
            pdf_bytes = source_path.read_bytes()
        else:
            raise ValidationError("file_source must be path-like or bytes")

        if not pdf_bytes.startswith(b"%PDF-"):
            raise ValidationError(
                "File does not appear to be a valid PDF (missing %PDF- header)"
            )

        size_bytes = len(pdf_bytes)
        # Heuristic page count estimation: count /Type /Page occurrences
        # Heuristic page count (defensive default to 1 if unexpected)
        estimated_pages = len(re.findall(rb"/Type\s*/Page(?!s)", pdf_bytes)) or 1

        actual_pages: Optional[int] = None
        if PdfReader is not None:
            try:
                actual_pages = len(PdfReader(io.BytesIO(pdf_bytes)).pages)
            except Exception:
                _LOG.warning("pdf_page_count_parse_failed", exc_info=True)
        page_count_for_logging = actual_pages or estimated_pages

        use_batch = size_bytes > SIZE_BATCH_THRESHOLD
        # Escalation: if heuristic says small but actual PDF may exceed limits, re-parse to get real page count.
        splitter_enabled = bool(self._cfg.doc_ai_splitter_id)
        if splitter_enabled and page_count_for_logging >= 199:
            _LOG.info(
                "docai_splitter_escalation",
                extra={
                    "estimated_pages": estimated_pages,
                    "actual_pages": actual_pages,
                    "size_bytes": size_bytes,
                    "splitter_processor": self._cfg.doc_ai_splitter_id,
                },
            )
            use_batch = True
        elif actual_pages is None and estimated_pages > PAGES_BATCH_THRESHOLD:
            use_batch = True

        if use_batch:
            _LOG.info(
                "docai_route_batch",
                extra={
                    "estimated_pages": estimated_pages,
                    "actual_pages": actual_pages,
                    "size_bytes": size_bytes,
                    "threshold_pages": PAGES_BATCH_THRESHOLD,
                    "threshold_size": SIZE_BATCH_THRESHOLD,
                    "batch_route": True,
                },
            )
            temp_path: Optional[Path] = None
            try:
                if source_path is None:
                    with tempfile.NamedTemporaryFile(
                        suffix=".pdf", delete=False
                    ) as tmp:
                        tmp.write(pdf_bytes)
                        temp_path = Path(tmp.name)
                    src_for_batch = str(temp_path)
                else:
                    src_for_batch = str(source_path)
                batch_result = batch_process_documents_gcs(
                    src_for_batch,
                    None,
                    self.processor_id,
                    location,
                    project_id=project_id,
                )
                meta = batch_result.setdefault("batch_metadata", {})
                meta.setdefault("batch_mode", "async_auto")
                meta.setdefault("pages_estimated", page_count_for_logging)
                return batch_result
            except ValidationError:
                raise
            except Exception as exc:
                raise OCRServiceError(f"Batch OCR failed: {exc}") from exc
            finally:
                if temp_path:
                    try:
                        temp_path.unlink(missing_ok=True)  # type: ignore[attr-defined]
                    except Exception:  # pragma: no cover - cleanup best effort
                        pass

        should_split = (actual_pages or estimated_pages) > PAGES_BATCH_THRESHOLD
        if should_split:
            if PdfReader is None or PdfWriter is None:
                raise OCRServiceError(
                    "pypdf is required to split oversized PDFs but is not available"
                )
            total_pages = actual_pages or estimated_pages
            _LOG.info(
                "Oversized PDF detected: %s pages",
                total_pages,
                extra={
                    "pages": total_pages,
                    "chunk_max_pages": DEFAULT_CHUNK_MAX_PAGES,
                    "size_bytes": size_bytes,
                    "processor": self.processor_id,
                },
            )
            chunk_limits = [
                DEFAULT_CHUNK_MAX_PAGES,
                FALLBACK_CHUNK_MAX_PAGES,
                MIN_CHUNK_MAX_PAGES,
            ]
            last_exc: Exception | None = None
            for attempt, chunk_limit in enumerate(chunk_limits, start=1):
                try:
                    return self._process_large_pdf(
                        pdf_bytes=pdf_bytes,
                        total_pages=total_pages,
                        size_bytes=size_bytes,
                        project_id=project_id,
                        location=location,
                        chunk_page_limit=chunk_limit,
                    )
                except _ChunkPageLimitExceeded as chunk_exc:
                    last_exc = chunk_exc
                    next_limit = (
                        chunk_limits[attempt] if attempt < len(chunk_limits) else None
                    )
                    _LOG.warning(
                        "docai_chunk_page_limit_exceeded",
                        extra={
                            "attempt": attempt,
                            "failed_limit": chunk_limit,
                            "next_limit": next_limit,
                            "total_pages": total_pages,
                            "size_bytes": size_bytes,
                            "error": str(chunk_exc),
                        },
                    )
                    continue
                except OCRServiceError as ocr_exc:
                    message = str(ocr_exc)
                    if (
                        "PAGE_LIMIT_EXCEEDED" in message
                        or "page limit" in message.lower()
                    ):
                        last_exc = ocr_exc
                        next_limit = (
                            chunk_limits[attempt]
                            if attempt < len(chunk_limits)
                            else None
                        )
                        _LOG.warning(
                            "docai_chunk_page_limit_exceeded",
                            extra={
                                "attempt": attempt,
                                "failed_limit": chunk_limit,
                                "next_limit": next_limit,
                                "total_pages": total_pages,
                                "size_bytes": size_bytes,
                                "error": message,
                            },
                        )
                        continue
                    raise
            raise OCRServiceError(
                "DocAI chunking failed after exhausting page limit fallbacks"
            ) from last_exc

        # Synchronous path (existing behaviour)
        return _process_chunk_with_docai(
            client=self._client,
            pdf_bytes=pdf_bytes,
            project_id=project_id,
            location=location,
            processor_id=self.processor_id,
            cfg=self._cfg,
        )

    def _process_large_pdf(
        self,
        *,
        pdf_bytes: bytes,
        total_pages: int,
        size_bytes: int,
        project_id: str,
        location: str,
        chunk_page_limit: int,
    ) -> Dict[str, Any]:
        try:
            chunks = _split_pdf_bytes(pdf_bytes, max_pages=chunk_page_limit)
        except Exception as exc:
            raise OCRServiceError(f"Failed splitting oversized PDF: {exc}") from exc

        if not chunks:
            raise OCRServiceError("PDF splitting produced no chunks")

        chunk_infos: list[dict[str, Any]] = []
        for idx, chunk in enumerate(chunks, start=1):
            start_page = (idx - 1) * chunk_page_limit + 1
            end_page = min(start_page + chunk_page_limit - 1, total_pages)
            chunk_infos.append(
                {
                    "index": idx,
                    "bytes": chunk,
                    "start_page": start_page,
                    "end_page": end_page,
                }
            )

        chunk_count = len(chunk_infos)
        results_by_index: dict[int, Dict[str, Any]] = {}
        with ThreadPoolExecutor(
            max_workers=min(MAX_CHUNK_CONCURRENCY, chunk_count)
        ) as executor:
            futures: list[tuple[Future[Dict[str, Any]], dict[str, Any]]] = []
            for info in chunk_infos:
                _LOG.info(
                    "Processing chunk %s/%s: pages %s-%s",
                    info["index"],
                    chunk_count,
                    info["start_page"],
                    info["end_page"],
                    extra={
                        "chunk_index": info["index"],
                        "chunk_total": chunk_count,
                        "chunk_start": info["start_page"],
                        "chunk_end": info["end_page"],
                        "chunk_page_limit": chunk_page_limit,
                        "size_bytes": size_bytes,
                    },
                )
                future = executor.submit(
                    _process_chunk_with_docai,
                    client=self._client,
                    pdf_bytes=info["bytes"],
                    project_id=project_id,
                    location=location,
                    processor_id=self.processor_id,
                    cfg=self._cfg,
                )
                futures.append((future, info))

            for future, info in futures:
                try:
                    chunk_result = future.result()
                except _ChunkPageLimitExceeded:
                    raise
                except Exception as exc:
                    raise OCRServiceError(
                        f"DocAI chunk {info['index']} failed: {exc}"
                    ) from exc
                results_by_index[info["index"]] = chunk_result or {}

        combined_pages: list[Dict[str, Any]] = []
        combined_texts: list[str] = []
        page_offset = 0

        for idx in sorted(results_by_index.keys()):
            chunk_result = results_by_index[idx]
            chunk_pages = chunk_result.get("pages", [])
            chunk_text = chunk_result.get("text", "")
            if chunk_text:
                combined_texts.append(_strip_noise(chunk_text))

            if chunk_pages:
                for page in chunk_pages:
                    page_number = page.get("page_number", 0)
                    absolute_page = (
                        page_number + page_offset
                        if page_number > 0
                        else len(combined_pages) + 1
                    )
                    combined_pages.append(
                        {
                            "page_number": absolute_page,
                            "text": _strip_noise(page.get("text", "")),
                        }
                    )
                page_offset += len(chunk_pages)
            elif chunk_text:
                fallback_page = {
                    "page_number": page_offset + 1,
                    "text": _strip_noise(chunk_text),
                }
                combined_pages.append(fallback_page)
                page_offset += 1

        if not combined_pages and combined_texts:
            combined_pages = [
                {"page_number": idx + 1, "text": text}
                for idx, text in enumerate(combined_texts)
            ]

        full_text = "\n".join(text for text in combined_texts if text)
        if not full_text and combined_pages:
            full_text = " ".join(page.get("text", "") for page in combined_pages)
        full_text = _strip_noise(full_text)

        return {"text": full_text, "pages": combined_pages}


def _resolve_state_store(
    job_id: str | None, state_store: PipelineStateStore | None
) -> PipelineStateStore | None:
    if not job_id:
        return None
    if state_store:
        return state_store
    try:
        return create_state_store_from_env()
    except Exception:  # pragma: no cover - fallback when state backend not configured
        _LOG.debug("state_store_resolution_failed", exc_info=True)
        return None


def _merge_metadata(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    merged.update(patch)
    return merged


def _split_gcs_uri(gcs_uri: str) -> tuple[str, str]:
    if not gcs_uri.startswith("gs://"):
        raise OCRServiceError("GCS URI must start with gs://")
    bucket, _, blob = gcs_uri[5:].partition("/")
    if not bucket or not blob:
        raise OCRServiceError("Invalid GCS URI; expected gs://bucket/object")
    return bucket, blob


def _gcs_upload_json(
    gcs_uri: str,
    payload: Dict[str, Any],
    *,
    if_generation_match: int | None = None,
) -> Optional[int]:
    try:
        from google.cloud import storage  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise OCRServiceError(f"google-cloud-storage unavailable: {exc}") from exc

    bucket_name, object_name = _split_gcs_uri(gcs_uri)
    client = storage.Client()
    cfg = get_config()
    kms_key = getattr(cfg, "cmek_key_name", None)
    blob = client.bucket(bucket_name).blob(object_name)
    if kms_key:
        setattr(blob, "kms_key_name", kms_key)
    upload_kwargs: Dict[str, Any] = {"content_type": "application/json"}
    if if_generation_match is not None:
        upload_kwargs["if_generation_match"] = if_generation_match
    blob.upload_from_string(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False), **upload_kwargs
    )
    return getattr(blob, "generation", None)


def _extract_gcs_output(result: Any) -> Optional[str]:
    if not result:
        return None
    if isinstance(result, dict):
        doc_cfg = (
            result.get("document_output_config")
            or result.get("documentOutputConfig")
            or {}
        )
        if isinstance(doc_cfg, dict):
            gcs_cfg = (
                doc_cfg.get("gcs_output_config") or doc_cfg.get("gcsOutputConfig") or {}
            )
            if isinstance(gcs_cfg, dict):
                uri = gcs_cfg.get("gcs_uri") or gcs_cfg.get("gcsUri")
                if uri:
                    return uri
        uri_direct = result.get("gcs_uri") or result.get("gcsUri")
        if isinstance(uri_direct, str):
            return uri_direct
    for attr in ("document_output_config", "documentOutputConfig"):
        doc_cfg = getattr(result, attr, None)
        if doc_cfg:
            gcs_cfg = getattr(doc_cfg, "gcs_output_config", None) or getattr(
                doc_cfg, "gcsOutputConfig", None
            )
            if gcs_cfg:
                uri = getattr(gcs_cfg, "gcs_uri", None) or getattr(
                    gcs_cfg, "gcsUri", None
                )
                if uri:
                    return uri
    metadata = getattr(result, "metadata", None)
    if metadata:
        return _extract_gcs_output(metadata)
    return None


def _normalise_shard_entry(entry: Any) -> Optional[str]:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        for key in ("gcs_uri", "gcsUri", "uri"):
            value = entry.get(key)
            if isinstance(value, str):
                return value
    for attr in ("gcs_uri", "gcsUri", "uri"):
        value = getattr(entry, attr, None)
        if isinstance(value, str):
            return value
    return None


def _extract_shards(result: Any, output_uri: str) -> list[str]:
    shards: list[str] = []
    candidates: Iterable[Any] = ()
    if isinstance(result, dict):
        for key in ("shards", "documents", "documentMetadata", "document_metadata"):
            value = result.get(key)
            if isinstance(value, Sequence):
                candidates = value
                break
    else:
        for attr in ("shards", "documents", "documentMetadata", "document_metadata"):
            value = getattr(result, attr, None)
            if isinstance(value, Sequence):
                candidates = value
                break
    for item in candidates:
        normalised = _normalise_shard_entry(item)
        if normalised:
            shards.append(normalised)
    if not shards:
        shards.append(output_uri.rstrip("/") + "/shard-000.pdf")
    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for shard in shards:
        if shard in seen:
            continue
        seen.add(shard)
        deduped.append(shard)
    return deduped


def _poll_operation(  # pylint: disable=too-many-arguments
    operation: Any,
    *,
    stage: str,
    job_id: str | None,
    trace_id: str | None,
    sleep_fn: Callable[[float], None] = time.sleep,
    initial_delay: float = 5.0,
    max_delay: float = 45.0,
    max_attempts: int = 60,
) -> Any:
    delay = initial_delay
    attempt = 0
    while True:
        attempt += 1
        try:
            if hasattr(operation, "result"):
                return operation.result(timeout=delay)
            if (
                hasattr(operation, "done") and operation.done()
            ):  # pragma: no cover - compatibility fallback
                return getattr(operation, "result", lambda: None)()
            return operation  # pragma: no cover - minimal fallback
        except gexc.DeadlineExceeded as exc:
            if attempt >= max_attempts:
                raise OCRServiceError(f"{stage} operation timed out") from exc
            wait_base = min(delay, max_delay)
            jitter = random.uniform(0.25 * wait_base, 0.75 * wait_base)
            sleep_seconds = wait_base + jitter
            _LOG.info(
                "docai_operation_retry",
                extra={
                    "stage": stage,
                    "attempt": attempt,
                    "sleep_seconds": round(sleep_seconds, 2),
                    "job_id": job_id,
                    "trace_id": trace_id,
                },
            )
            sleep_fn(sleep_seconds)
            delay = min(delay * 1.6, max_delay)
            continue
        except (
            gexc.GoogleAPICallError
        ) as exc:  # pragma: no cover - network specific handling
            raise OCRServiceError(f"{stage} operation failed: {exc}") from exc
        except Exception as exc:  # pragma: no cover - defensive wrap
            raise OCRServiceError(f"{stage} operation error: {exc}") from exc


def run_splitter(  # pylint: disable=too-many-arguments
    gcs_uri: str,
    *,
    processor_id: str | None = None,
    project_id: str | None = None,
    location: str | None = None,
    output_bucket: str | None = None,
    output_prefix: str | None = None,
    manifest_name: str = "split.json",
    job_id: str | None = None,
    trace_id: str | None = None,
    state_store: PipelineStateStore | None = None,
    client: Any | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    if not gcs_uri:
        raise ValidationError("gcs_uri required")
    cfg = get_config()
    project_id = project_id or cfg.project_id
    location = location or getattr(cfg, "doc_ai_location", cfg.region)
    processor_id = processor_id or cfg.doc_ai_splitter_id
    if not processor_id:
        raise OCRServiceError("DOC_AI_SPLITTER_PROCESSOR_ID not configured")
    output_bucket = output_bucket or cfg.intake_gcs_bucket
    base_prefix = output_prefix or f"split/{job_id or uuid.uuid4().hex}/"
    destination_uri = f"gs://{output_bucket.rstrip('/')}/{base_prefix.lstrip('/')}"
    _LOG.info(
        "docai_split_start",
        extra={
            "input": gcs_uri,
            "output_prefix": destination_uri,
            "processor": processor_id,
            "job_id": job_id,
            "trace_id": trace_id,
        },
    )
    client = client or documentai.DocumentProcessorServiceClient(
        client_options=ClientOptions(
            api_endpoint=f"{location}-documentai.googleapis.com"
        )
    )
    kms_key = getattr(cfg, "cmek_key_name", None)
    gcs_output: Dict[str, Any] = {"gcs_uri": destination_uri}
    if kms_key:
        gcs_output["kms_key_name"] = kms_key
    request = {
        "name": f"projects/{project_id}/locations/{location}/processors/{processor_id}",
        "input_documents": {
            "gcs_documents": {
                "documents": [{"gcs_uri": gcs_uri, "mime_type": "application/pdf"}]
            }
        },
        "document_output_config": {"gcs_output_config": gcs_output},
    }
    if kms_key:
        request["encryption_spec"] = {"kms_key_name": kms_key}
    resolved_store = _resolve_state_store(job_id, state_store)
    job_snapshot = None
    if resolved_store and job_id:
        try:
            resolved_store.mark_status(
                job_id,
                PipelineStatus.SPLIT_SCHEDULED,
                stage="DOC_AI_SPLITTER",
                message="Splitter job started",
                extra={"input_uri": gcs_uri, "output_uri": destination_uri},
            )
        except Exception:
            _LOG.exception(
                "split_state_mark_start_failed",
                extra={"job_id": job_id, "trace_id": trace_id},
            )
        try:
            job_snapshot = resolved_store.get_job(job_id)
        except Exception:  # pragma: no cover - defensive read
            _LOG.exception(
                "split_state_snapshot_failed",
                extra={"job_id": job_id, "trace_id": trace_id},
            )
            job_snapshot = None
    started_at = time.perf_counter()
    operation = client.batch_process_documents(request=request)
    try:
        result = _poll_operation(
            operation,
            stage="docai_splitter",
            job_id=job_id,
            trace_id=trace_id,
            sleep_fn=sleep_fn,
        )
    except Exception as exc:
        if resolved_store and job_id:
            try:
                resolved_store.mark_status(
                    job_id,
                    PipelineStatus.FAILED,
                    stage="DOC_AI_SPLITTER",
                    message=str(exc),
                    extra={"input_uri": gcs_uri},
                    updates={
                        "last_error": {"stage": "docai_splitter", "error": str(exc)}
                    },
                )
            except Exception:
                _LOG.exception(
                    "split_state_mark_failure_failed",
                    extra={"job_id": job_id, "trace_id": trace_id},
                )
        publish_pipeline_failure(
            stage="DOC_AI_SPLITTER",
            job_id=job_id,
            trace_id=trace_id,
            error=exc,
            metadata={"input_uri": gcs_uri},
        )
        raise
    duration_ms = int((time.perf_counter() - started_at) * 1000)
    output_uri = _extract_gcs_output(result) or destination_uri
    shards = _extract_shards(result, output_uri)
    manifest_uri = output_uri.rstrip("/") + f"/{manifest_name}"
    try:
        _gcs_upload_json(
            manifest_uri,
            {
                "shards": shards,
                "source": gcs_uri,
                "operation": getattr(operation, "name", None),
            },
            if_generation_match=0,
        )
    except Exception as exc:  # pragma: no cover - best effort
        _LOG.warning(
            "split_manifest_write_failed",
            extra={"manifest_uri": manifest_uri, "error": str(exc)},
        )

    if resolved_store and job_id:
        try:
            job_snapshot = job_snapshot or resolved_store.get_job(job_id)
            metadata_base: Dict[str, Any] = {}
            if job_snapshot:
                metadata_base = dict(job_snapshot.metadata)
            metadata_patch = {
                "split_manifest_uri": manifest_uri,
                "split_output_uri": output_uri,
                "split_shards": shards,
            }
            resolved_store.mark_status(
                job_id,
                PipelineStatus.SPLIT_DONE,
                stage="DOC_AI_SPLITTER",
                message="Splitter job complete",
                extra={"shard_count": len(shards)},
                updates={"metadata": _merge_metadata(metadata_base, metadata_patch)},
            )
        except Exception:
            _LOG.exception(
                "split_state_mark_complete_failed",
                extra={"job_id": job_id, "trace_id": trace_id},
            )
    attempt_value = 1
    if job_snapshot and isinstance(job_snapshot.retries, dict):
        attempt_value = job_snapshot.retries.get("DOC_AI_SPLITTER", 0) + 1
    log_extra: Dict[str, Any] = {
        "job_id": job_id,
        "trace_id": trace_id,
        "document_id": gcs_uri,
        "shard_id": "aggregate",
        "duration_ms": duration_ms,
        "schema_version": cfg.summary_schema_version,
        "attempt": attempt_value,
        "component": "docai_splitter",
        "severity": "INFO",
        "manifest_uri": manifest_uri,
        "shard_count": len(shards),
    }
    if trace_id:
        log_extra["logging.googleapis.com/trace"] = (
            f"projects/{project_id}/traces/{trace_id}"
        )
    _LOG.info("split_done", extra=log_extra)
    return {
        "operation": getattr(operation, "name", None),
        "output_uri": output_uri,
        "manifest_uri": manifest_uri,
        "shards": shards,
    }


def run_batch_ocr(  # pylint: disable=too-many-arguments
    shards: Sequence[str],
    *,
    processor_id: str | None = None,
    project_id: str | None = None,
    location: str | None = None,
    output_bucket: str | None = None,
    output_prefix: str | None = None,
    job_id: str | None = None,
    trace_id: str | None = None,
    state_store: PipelineStateStore | None = None,
    client: Any | None = None,
    max_concurrency: int | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    if not shards:
        raise ValidationError("shards must be a non-empty sequence")
    cfg = get_config()
    project_id = project_id or cfg.project_id
    location = location or getattr(cfg, "doc_ai_location", cfg.region)
    processor_id = processor_id or cfg.doc_ai_processor_id
    if not processor_id:
        raise OCRServiceError("DOC_AI_PROCESSOR_ID not configured")
    output_bucket = output_bucket or cfg.output_gcs_bucket
    base_prefix = output_prefix or f"ocr/{job_id or uuid.uuid4().hex}/"

    if max_concurrency is None:
        max_concurrency = max(1, getattr(cfg, "max_shard_concurrency", 4))

    client = client or documentai.DocumentProcessorServiceClient(
        client_options=ClientOptions(
            api_endpoint=f"{location}-documentai.googleapis.com"
        )
    )
    resolved_store = _resolve_state_store(job_id, state_store)
    if resolved_store and job_id:
        try:
            resolved_store.mark_status(
                job_id,
                PipelineStatus.OCR_SCHEDULED,
                stage="DOC_AI_OCR",
                message="OCR fan-out scheduled",
                extra={"shard_count": len(shards)},
            )
        except Exception:
            _LOG.exception(
                "ocr_state_mark_start_failed",
                extra={"job_id": job_id, "trace_id": trace_id},
            )

    outputs: list[Dict[str, Any]] = []
    inflight: list[tuple[int, str, str, Any, float]] = []
    shard_iter = iter(enumerate(shards))
    attempt_value = 1
    job_snapshot = None
    if resolved_store and job_id:
        try:
            job_snapshot = resolved_store.get_job(job_id)
        except Exception:
            _LOG.exception(
                "ocr_state_snapshot_failed",
                extra={"job_id": job_id, "trace_id": trace_id},
            )
    if job_snapshot and isinstance(job_snapshot.retries, dict):
        attempt_value = job_snapshot.retries.get("DOC_AI_OCR", 0) + 1

    def _enqueue_next() -> bool:
        try:
            shard_index, shard_uri = next(shard_iter)
        except StopIteration:
            return False
        dest_uri = f"gs://{output_bucket.rstrip('/')}/{base_prefix.lstrip('/')}{shard_index:04d}/"
        kms_key = getattr(cfg, "cmek_key_name", None)
        output_config: Dict[str, Any] = {"gcs_uri": dest_uri}
        if kms_key:
            output_config["kms_key_name"] = kms_key
        request = {
            "name": f"projects/{project_id}/locations/{location}/processors/{processor_id}",
            "input_documents": {
                "gcs_documents": {
                    "documents": [
                        {"gcs_uri": shard_uri, "mime_type": "application/pdf"}
                    ]
                }
            },
            "document_output_config": {"gcs_output_config": output_config},
        }
        if kms_key:
            request["encryption_spec"] = {"kms_key_name": kms_key}
        operation = client.batch_process_documents(request=request)
        started_at = time.perf_counter()
        inflight.append((shard_index, shard_uri, dest_uri, operation, started_at))
        log_extra = {
            "job_id": job_id,
            "trace_id": trace_id,
            "document_id": shard_uri,
            "shard_id": f"{shard_index:04d}",
            "duration_ms": 0,
            "schema_version": cfg.summary_schema_version,
            "attempt": attempt_value,
            "component": "docai_ocr",
            "severity": "INFO",
        }
        if trace_id:
            log_extra["logging.googleapis.com/trace"] = (
                f"projects/{project_id}/traces/{trace_id}"
            )
        _LOG.info("ocr_lro_started", extra=log_extra)
        return True

    for _ in range(min(max_concurrency, len(shards))):
        if not _enqueue_next():
            break

    while inflight:
        shard_index, shard_uri, dest_uri, operation, started_at = inflight.pop(0)
        try:
            result = _poll_operation(
                operation,
                stage="docai_ocr",
                job_id=job_id,
                trace_id=trace_id,
                sleep_fn=sleep_fn,
            )
        except Exception as exc:
            if resolved_store and job_id:
                try:
                    resolved_store.mark_status(
                        job_id,
                        PipelineStatus.FAILED,
                        stage="DOC_AI_OCR",
                        message=str(exc),
                        extra={"shard_uri": shard_uri},
                        updates={
                            "last_error": {
                                "stage": "ocr",
                                "shard_uri": shard_uri,
                                "error": str(exc),
                            }
                        },
                    )
                except Exception:
                    _LOG.exception(
                        "ocr_state_mark_failure_failed",
                        extra={"job_id": job_id, "trace_id": trace_id},
                    )
            publish_pipeline_failure(
                stage="DOC_AI_OCR",
                job_id=job_id,
                trace_id=trace_id,
                error=exc,
                metadata={"shard_uri": shard_uri},
            )
            raise

        output_uri = _extract_gcs_output(result) or dest_uri
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        log_extra = {
            "job_id": job_id,
            "trace_id": trace_id,
            "document_id": shard_uri,
            "shard_id": f"{shard_index:04d}",
            "duration_ms": duration_ms,
            "schema_version": cfg.summary_schema_version,
            "attempt": attempt_value,
            "component": "docai_ocr",
            "severity": "INFO",
            "ocr_output_uri": output_uri,
        }
        if trace_id:
            log_extra["logging.googleapis.com/trace"] = (
                f"projects/{project_id}/traces/{trace_id}"
            )
        _LOG.info("ocr_lro_finished", extra=log_extra)
        outputs.append(
            {
                "shard_uri": shard_uri,
                "ocr_output_uri": output_uri,
                "operation": getattr(operation, "name", None),
            }
        )

        if max_concurrency > 1:
            while len(inflight) < max_concurrency:
                if not _enqueue_next():
                    break

    if resolved_store and job_id:
        try:
            job_snapshot = resolved_store.get_job(job_id)
            metadata_base: Dict[str, Any] = {}
            if job_snapshot:
                metadata_base = dict(job_snapshot.metadata)
            metadata_patch = {"ocr_outputs": outputs}
            resolved_store.mark_status(
                job_id,
                PipelineStatus.OCR_DONE,
                stage="DOC_AI_OCR",
                message="OCR fan-out complete",
                extra={"shard_count": len(outputs)},
                updates={"metadata": _merge_metadata(metadata_base, metadata_patch)},
            )
        except Exception:
            _LOG.exception(
                "ocr_state_mark_complete_failed",
                extra={"job_id": job_id, "trace_id": trace_id},
            )

    return {"outputs": outputs}


__all__ = ["OCRService", "run_splitter", "run_batch_ocr", "clean_ocr_output"]
