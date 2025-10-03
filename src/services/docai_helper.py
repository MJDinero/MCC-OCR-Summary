# pylint: disable=duplicate-code,R0801
"""Document AI helper utilities with fallback shims.
This module intentionally mirrors patterns used in other agents; duplicate-code is disabled here.
"""
from __future__ import annotations
import logging
import time
from typing import Dict
import backoff
from google.api_core.client_options import ClientOptions
from google.api_core.exceptions import GoogleAPICallError, InvalidArgument
from google.cloud import documentai_v1 as documentai
from prometheus_client import Counter, Histogram
from mcc.config import get_config, AppConfig
from mcc.utils.docai_request_builder import build_docai_request

# Safe tracing + status shims
try:  # pragma: no cover - test env may lack OTEL
    from mcc.utils.tracing_v2 import get_tracer, Status, StatusCode  # type: ignore
except Exception:  # pylint: disable=broad-except
    from contextlib import nullcontext
    def get_tracer(_name: str):  # type: ignore
        class _NoTracer:
            def start_as_current_span(self, *_a, **_k):
                return nullcontext()
        return _NoTracer()
    class StatusCode:  # minimal shim
        OK = 0
        ERROR = 1
    class Status:  # shim
        def __init__(self, status_code, description: str | None = None):  # pylint: disable=unused-argument
            self.status_code = status_code
            self.description = description


_DOC_CALLS = Counter("docai_calls_total", "DocAI calls", ["status"])
_DOC_LATENCY = Histogram("docai_latency_sec", "Latency for DocAI requests")
_LOG = logging.getLogger("DocAIHelper")
_TRACER = get_tracer(__name__)


class DocAISettings(AppConfig):  # thin alias for backwards compat export present in __all__
    pass


@backoff.on_exception(backoff.expo, (GoogleAPICallError, InvalidArgument), max_tries=4)
def process_document(
    file_path: str,
    processor_id: str,
    *,
    cfg: AppConfig | None = None,
) -> Dict:
    """
    Call a DocAI processor and return the document as a dict.
    Args:
        file_path: Path to the PDF file.
        config: Configuration for Document AI.
    Returns:
        dict: Document AI result.
    Raises:
        GoogleAPICallError, InvalidArgument
    """
    cfg = cfg or get_config()
    location = cfg.doc_ai_location
    project_id = cfg.effective_project
    endpoint = f"{location}-documentai.googleapis.com"
    client = documentai.DocumentProcessorServiceClient(
        client_options=ClientOptions(api_endpoint=endpoint)
    )
    name, request = build_docai_request(file_path, project_id, location, processor_id)
    with _TRACER.start_as_current_span("docai.process_document") as span:
        span.set_attribute("processor_id", processor_id)
        start_time = time.perf_counter()
        try:
            _DOC_CALLS.labels(status="success").inc()
            result = client.process_document(request=request)
            span.set_status(Status(StatusCode.OK))
            return documentai.Document.to_dict(result.document)
        except (GoogleAPICallError, InvalidArgument) as err:
            _DOC_CALLS.labels(status="retry").inc()
            span.record_exception(err)
            span.set_status(Status(StatusCode.ERROR, str(err)))
            raise
        finally:
            latency = time.perf_counter() - start_time
            _DOC_LATENCY.observe(latency)
            _LOG.debug("docai_latency", sec=round(latency, 4))


__all__ = ["DocAISettings", "process_document"]
