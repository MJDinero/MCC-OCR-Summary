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
from typing import Any, Callable, Dict, Optional, Protocol, Tuple

from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
from google.api_core.client_options import ClientOptions
from google.api_core import exceptions as gexc
from google.cloud import documentai_v1 as documentai

from src.config import get_config, AppConfig
from src.utils.docai_request_builder import build_docai_request
from src.errors import OCRServiceError, ValidationError

_LOG = logging.getLogger("ocr_service")

try:  # pragma: no cover - optional metrics
    from prometheus_client import Counter, Histogram  # type: ignore
    _OCR_CALLS = Counter("ocr_service_calls_total", "Total OCR service invocations", ["status"])
    _OCR_LATENCY = Histogram("ocr_service_latency_seconds", "OCR service latency seconds")
except Exception:  # pragma: no cover
    _OCR_CALLS = None  # type: ignore
    _OCR_LATENCY = None  # type: ignore


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
    except Exception:  # pragma: no cover
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
        self._endpoint = f"{self._cfg.doc_ai_location}-documentai.googleapis.com"
        self._client = self._client_factory(self._endpoint)

    def close(self) -> None:  # pragma: no cover - underlying client close may not be needed
        close = getattr(self._client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # swallow close errors
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
        project_id = self._cfg.effective_project
        location = self._cfg.doc_ai_location

        try:
            name, request = build_docai_request(
                file_source, project_id, location, self.processor_id
            )
        except ValidationError:
            raise  # propagate directly
        except Exception as exc:  # wrap any other
            raise OCRServiceError(f"Failed building request: {exc}") from exc

        start = time.perf_counter()
        try:
            result = self._client.process_document(request=request)
            if _OCR_CALLS:
                _OCR_CALLS.labels(status="success").inc()
        except (gexc.ServiceUnavailable, gexc.DeadlineExceeded):  # will be retried by tenacity
            _LOG.warning("Transient DocAI failure; will retry", exc_info=True)
            if _OCR_CALLS:
                _OCR_CALLS.labels(status="transient_error").inc()
            raise
        except gexc.GoogleAPICallError as exc:
            if _OCR_CALLS:
                _OCR_CALLS.labels(status="permanent_error").inc()
            raise OCRServiceError(f"Permanent DocAI failure: {exc}") from exc
        except Exception as exc:  # pragma: no cover - unexpected library errors
            if _OCR_CALLS:
                _OCR_CALLS.labels(status="unexpected_error").inc()
            raise OCRServiceError(f"Unexpected OCR error: {exc}") from exc
        finally:
            elapsed = time.perf_counter()-start
            if _OCR_LATENCY:
                _OCR_LATENCY.observe(elapsed)
            _LOG.debug("docai_process_attempt", elapsed_ms=round(elapsed*1000,2))

        raw_doc = _extract_document_dict(result)
        return _normalise(raw_doc)


__all__ = ["OCRService"]
