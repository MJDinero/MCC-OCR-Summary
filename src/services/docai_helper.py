"""Document AI OCR service abstraction with retry & dependency injection.

This module exposes an `OCRService` that can be injected into the API layer or
other services. The implementation wraps Google Document AI but shields callers
from library specific concerns (request construction, transient retries, result
normalisation) and converts errors into internal domain exceptions.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import time
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Protocol

from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
from google.api_core.client_options import ClientOptions
from google.api_core import exceptions as gexc
from google.cloud import documentai_v1 as documentai

from src.config import get_config, AppConfig
from src.utils.docai_request_builder import build_docai_request
from src.errors import OCRServiceError, ValidationError
from src.services.docai_batch_helper import batch_process_documents_gcs
from src.utils.pdf_splitter import split_pdf_by_page_limit

_LOG = logging.getLogger("ocr_service")


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

    # Real object path: documentai.Document
    try:  # pragma: no cover - best effort
        if hasattr(doc, "to_dict"):
            return doc.to_dict()  # type: ignore
    except (AttributeError, TypeError, ValueError):  # pragma: no cover - narrow expected issues
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
        pages_out.append({"page_number": idx, "text": text})
    full_text = doc.get("text") or " ".join(pg["text"] for pg in pages_out)
    return {"text": full_text, "pages": pages_out}


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
        # Build regional endpoint (region previously doc_ai_location)
        self._endpoint = f"{self._cfg.region}-documentai.googleapis.com"
        self._client = self._client_factory(self._endpoint)

    def close(self) -> None:  # pragma: no cover - underlying client close may not be needed
        close_attr = getattr(self._client, "close", None)
        if not callable(close_attr):  # nothing to do
            return
        try:
            close_attr()  # type: ignore[misc]
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
        location = self._cfg.region

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
            raise ValidationError("File does not appear to be a valid PDF (missing %PDF- header)")

        size_bytes = len(pdf_bytes)
        # Heuristic page count estimation: count /Type /Page occurrences
        # Heuristic page count (defensive default to 1 if unexpected)
        page_count = len(re.findall(rb"/Type\s*/Page(?!s)", pdf_bytes)) or 1

        use_batch = page_count > PAGES_BATCH_THRESHOLD or size_bytes > SIZE_BATCH_THRESHOLD
        # Escalation: if heuristic says small but actual PDF may exceed limits, re-parse to get real page count.
        if not use_batch:
            try:
                from PyPDF2 import PdfReader  # lazy import
                try:  # import specific error classes if available
                    from PyPDF2.errors import PdfReadError, EmptyFileError  # type: ignore
                except Exception:  # pragma: no cover - fallback when errors module layout differs
                    PdfReadError = EmptyFileError = RuntimeError  # type: ignore
                tmp_bytes = pdf_bytes  # local alias
                # Only materialize file to parse if we got bytes
                if source_path is None:
                    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as _tmpf:
                        _tmpf.write(tmp_bytes)
                        real_reader = PdfReader(_tmpf.name)
                else:
                    real_reader = PdfReader(str(source_path))
                real_pages = len(real_reader.pages)
                if real_pages != page_count:
                    _LOG.info(
                        "page_count_adjusted",
                        extra={
                            "heuristic_pages": page_count,
                            "actual_pages": real_pages,
                            "size_bytes": size_bytes,
                        },
                    )
                # Force escalation: any actual_pages > 30 must go async; >=199 triggers split.
                if real_pages > 30:
                    use_batch = True
                    page_count = real_pages
                    if real_pages >= 199:
                        _LOG.info(
                            "escalation_forced_split",
                            extra={"actual_pages": real_pages, "split_threshold": 199},
                        )
                    else:
                        _LOG.info(
                            "escalation_forced_batch",
                            extra={"actual_pages": real_pages, "batch_threshold": 30},
                        )
            except (OSError, ValueError, RuntimeError, PdfReadError, EmptyFileError) as exc:  # pragma: no cover - best effort
                _LOG.debug("page_count_escalation_failed", extra={"error": str(exc)})
        if use_batch:
            _LOG.info(
                "detected_large_pdf",
                extra={
                    "page_count": page_count,
                    "threshold": 199,
                    "size_bytes": size_bytes,
                    "batch_route": True,
                },
            )
            _LOG.info(
                "docai_route_batch",
                extra={
                    "estimated_pages": page_count,
                    "size_bytes": size_bytes,
                    "threshold_pages": PAGES_BATCH_THRESHOLD,
                    "threshold_size": SIZE_BATCH_THRESHOLD,
                },
            )
            # Persist to temp file if bytes provided so batch helper can upload
            if source_path is None:
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(pdf_bytes)
                    tmp_path = Path(tmp.name)
                src_for_batch = str(tmp_path)
            else:
                src_for_batch = str(source_path)
            # Force splitting for page_count >= 199 to avoid PAGE_LIMIT_EXCEEDED in Document AI.
            if page_count >= 199:
                try:
                    split = split_pdf_by_page_limit(src_for_batch, max_pages=199)
                except Exception as exc:  # pragma: no cover - unexpected split errors
                    raise OCRServiceError(f"PDF split failed: {exc}") from exc
                aggregated_pages = []
                aggregated_text_parts = []
                part_index = 0
                _LOG.info(
                    "batch_sequence_start",
                    extra={"parts": len(split.parts), "manifest": split.manifest_gcs_uri},
                )
                for part_uri in split.parts:
                    part_index += 1
                    try:
                        result = batch_process_documents_gcs(
                            part_uri,
                            None,
                            self.processor_id,
                            location,
                            project_id=project_id,
                        )
                    except ValidationError:
                        raise
                    except Exception as exc:
                        raise OCRServiceError(f"Batch OCR failed for split part {part_index}: {exc}") from exc
                    # Merge pages & text
                    pages = result.get("pages") or []
                    aggregated_pages.extend(pages)
                    aggregated_text_parts.append(result.get("text", ""))
                merged = {
                    "text": "\n".join(t for t in aggregated_text_parts if t),
                    "pages": aggregated_pages,
                    "batch_metadata": {
                        "status": "succeeded",
                        "parts": len(split.parts),
                        "split_manifest": split.manifest_gcs_uri,
                        "batch_mode": "async_split",
                    },
                }
                # Emit specific aggregated completion event (distinct from per-part batch_complete events)
                _LOG.info(
                    "batch_complete",  # retained for backward compatibility in log queries
                    extra={"pages": len(aggregated_pages), "parts": len(split.parts), "aggregated": True},
                )
                _LOG.info(
                    "aggregated_batch_complete",
                    extra={
                        "total_pages": len(aggregated_pages),
                        "parts": len(split.parts),
                        "split_manifest": split.manifest_gcs_uri,
                        "batch_mode": "async_split",
                    },
                )
                return merged
            # Output URI base left None to auto-generate unique prefix for single large file
            try:
                batch_result = batch_process_documents_gcs(
                    src_for_batch,
                    None,
                    self.processor_id,
                    location,
                    project_id=project_id,
                )
                # Tag single batch path as async_single for observability
                meta = batch_result.setdefault("batch_metadata", {})
                meta.setdefault("batch_mode", "async_single")
                return batch_result
            except ValidationError:
                raise
            except Exception as exc:
                raise OCRServiceError(f"Batch OCR failed: {exc}") from exc

        # Synchronous path (existing behaviour)
        try:
            _name, request = build_docai_request(
                pdf_bytes, project_id, location, self.processor_id
            )
        except ValidationError:
            raise  # propagate directly
        except Exception as exc:  # wrap any other
            raise OCRServiceError(f"Failed building request: {exc}") from exc

        start = time.perf_counter()
        try:
            result = self._client.process_document(request=request)
        except (gexc.ServiceUnavailable, gexc.DeadlineExceeded):  # will be retried by tenacity
            _LOG.warning("Transient DocAI failure; will retry", exc_info=True)
            raise
        except gexc.GoogleAPICallError as exc:
            raise OCRServiceError(f"Permanent DocAI failure: {exc}") from exc
        except Exception as exc:  # pragma: no cover - unexpected library errors
            raise OCRServiceError(f"Unexpected OCR error: {exc}") from exc
        finally:
            elapsed = time.perf_counter() - start
            _LOG.debug("docai_process_attempt", extra={"elapsed_ms": round(elapsed*1000,2)})

        raw_doc = _extract_document_dict(result)
        return _normalise(raw_doc)


__all__ = ["OCRService"]
