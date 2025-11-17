"""Composable pipeline service for OCR → summary → PDF delivery workflow."""

from __future__ import annotations

import inspect
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Protocol, Sequence, Tuple

from src.errors import (
    DriveServiceError,
    PDFGenerationError,
    SummarizationError,
    ValidationError,
    PdfValidationError,
)
from src.services.docai_helper import clean_ocr_output
from src.services.interfaces import MetricsClient
from src.services.metrics import NullMetrics
from src.services.supervisor import CommonSenseSupervisor
from src.services.summarization import build_pdf_sections_from_payload
from src.services.bible import FORBIDDEN_PDF_PHRASES
from src.utils.logging_utils import structured_log
from src.utils.pipeline_failures import publish_pipeline_failure
from src.utils.summary_thresholds import compute_summary_min_chars

_LOG = logging.getLogger("process_pipeline")
class PdfDeliveryService(Protocol):
    """Interface for delivering generated PDFs to downstream destinations."""

    def deliver_pdf(
        self,
        payload: bytes,
        *,
        trace_id: str | None = None,
        source: str | None = None,
        folder_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """Persist the PDF bytes and return the identifier (Drive ID, URI, etc.)."""


@dataclass(slots=True)
class ProcessPipelineResult:
    pdf_bytes: bytes
    validation: Dict[str, Any]
    drive_file_id: str | None


class ProcessPipelineService:
    """High-level orchestrator for OCR → summary → PDF compose → delivery."""

    def __init__(
        self,
        *,
        ocr_service: Any,
        summariser: Any,
        pdf_writer: Any,
        pdf_delivery: PdfDeliveryService,
        drive_report_folder_id: str | None,
        stub_mode: bool,
        supervisor_simple: bool,
        summary_compose_mode: str,
        pdf_writer_mode: str,
        writer_backend: str,
        metrics: MetricsClient | None = None,
    ) -> None:
        self._ocr = ocr_service
        self._summariser = summariser
        self._pdf_writer = pdf_writer
        self._pdf_delivery = pdf_delivery
        self._drive_report_folder_id = drive_report_folder_id
        self._stub_mode = stub_mode
        self._min_ocr_chars = 0 if stub_mode else int(os.getenv("MIN_OCR_CHARS", "50"))
        self._supervisor = CommonSenseSupervisor(simple=supervisor_simple)
        self._summary_compose_mode = summary_compose_mode
        self._pdf_writer_mode = pdf_writer_mode
        self._writer_backend = writer_backend
        self._metrics = metrics or NullMetrics()

    async def run(
        self,
        *,
        pdf_bytes: bytes,
        source: str,
        trace_id: str | None,
        guard_enabled: bool,
        request_context: dict[str, Any] | None = None,
    ) -> ProcessPipelineResult:
        context = request_context or {}
        self._log_components(trace_id=trace_id, source=source, context=context)

        if not pdf_bytes:
            raise ValidationError("Uploaded file empty")
        if not self._stub_mode and not pdf_bytes.startswith(b"%PDF-"):
            raise ValidationError("File must be a PDF")

        ocr_result = self._run_ocr(pdf_bytes=pdf_bytes, trace_id=trace_id, source=source)
        ocr_text = (ocr_result.get("text") or "").strip()
        ocr_len = len(ocr_text)
        if ocr_len < self._min_ocr_chars:
            structured_log(
                _LOG,
                logging.ERROR,
                "ocr_too_short",
                trace_id=trace_id,
                source=source,
                text_length=ocr_len,
                min_required=self._min_ocr_chars,
            )
            raise ValidationError("OCR extraction insufficient")

        cleaned_ocr_text = clean_ocr_output(ocr_text)
        summary_source_text = cleaned_ocr_text or ocr_text
        summary_dict = await self._run_summary(
            summary_source_text,
            ocr_len=ocr_len,
            stub_mode=self._stub_mode,
            trace_id=trace_id,
            source=source,
        )

        validation, sections = self._supervise_output(
            ocr_result=ocr_result,
            summary_dict=summary_dict,
            pdf_bytes=pdf_bytes,
            guard_enabled=guard_enabled,
            trace_id=trace_id,
        )

        pdf_payload = self._build_pdf(
            title=_derive_title(ocr_result, summary_dict),
            sections=sections,
            trace_id=trace_id,
            source=source,
        )

        drive_file_id = self._deliver_pdf(
            payload=pdf_payload,
            trace_id=trace_id,
            source=source,
        )

        structured_log(
            _LOG,
            logging.INFO,
            "process_complete",
            trace_id=trace_id,
            source=source,
            supervisor_passed=validation.get("supervisor_passed"),
            summary_chars=len(
                " ".join(
                    v for v in summary_dict.values() if isinstance(v, str)
                ).strip()
            ),
            pdf_bytes=len(pdf_payload),
            pdf_compliant=validation.get("pdf_compliant"),
            forbidden_phrases=validation.get("pdf_forbidden_phrases"),
            drive_file_id=drive_file_id,
        )
        return ProcessPipelineResult(
            pdf_bytes=pdf_payload,
            validation=validation,
            drive_file_id=drive_file_id,
        )

    def _log_components(
        self, *, trace_id: str | None, source: str, context: dict[str, Any]
    ) -> None:
        summariser_backend = getattr(self._summariser, "backend", None)
        structured_log(
            _LOG,
            logging.INFO,
            "pipeline_components_in_use",
            trace_id=trace_id,
            request_path=context.get("path"),
            request_method=context.get("method"),
            source=source,
            summary_compose_mode=self._summary_compose_mode,
            summariser_class=self._summariser.__class__.__name__,
            summariser_backend=(
                summariser_backend.__class__.__name__
                if summariser_backend
                else "None"
            ),
            pdf_writer_mode=self._pdf_writer_mode,
            pdf_writer_backend=self._writer_backend,
        )

    def _run_ocr(
        self, *, pdf_bytes: bytes, trace_id: str | None, source: str
    ) -> Dict[str, Any]:
        ocr_kwargs: dict[str, Any] = {}
        process_params = inspect.signature(self._ocr.process).parameters
        if "trace_id" in process_params:
            ocr_kwargs["trace_id"] = trace_id
        try:
            ocr_result = self._ocr.process(pdf_bytes, **ocr_kwargs)
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            structured_log(
                _LOG,
                logging.ERROR,
                "ocr_failure",
                trace_id=trace_id,
                source=source,
                error=str(exc),
            )
            raise

        ocr_text = (ocr_result.get("text") or "").strip()
        pages = ocr_result.get("pages") or []
        structured_log(
            _LOG,
            logging.INFO,
            "ocr_success",
            trace_id=trace_id,
            source=source,
            text_length=len(ocr_text),
            pages=len(pages),
        )
        return ocr_result

    async def _run_summary(
        self,
        text: str,
        *,
        ocr_len: int,
        stub_mode: bool,
        trace_id: str | None,
        source: str,
    ) -> Dict[str, Any]:
        try:
            summary_raw = await self._summariser.summarise_async(text)
        except SummarizationError as exc:
            self._metrics.increment("summarisation_failures", stage="summary")
            publish_pipeline_failure(
                stage="SUMMARY_JOB",
                job_id=None,
                trace_id=trace_id,
                error=exc,
                metadata={"source": source},
            )
            structured_log(
                _LOG,
                logging.ERROR,
                "summary_failure",
                trace_id=trace_id,
                source=source,
                error=str(exc),
            )
            raise

        summary_dict = (
            summary_raw
            if isinstance(summary_raw, dict)
            else {"Medical Summary": str(summary_raw or "")}
        )
        summary_text_fragments = [
            value for value in summary_dict.values() if isinstance(value, str)
        ]
        summary_text = "\n".join(summary_text_fragments).strip()
        summary_len = len(summary_text)
        min_summary_chars = compute_summary_min_chars(ocr_len, stub_mode=stub_mode)
        if summary_len < min_summary_chars:
            self._metrics.increment("summarisation_failures", stage="summary")
            structured_log(
                _LOG,
                logging.ERROR,
                "summary_too_short",
                trace_id=trace_id,
                summary_length=summary_len,
                min_required=min_summary_chars,
                ocr_length=ocr_len,
                source=source,
            )
            raise SummarizationError("Summary generation failed validation")
        return dict(summary_dict)

    def _supervise_output(
        self,
        *,
        ocr_result: Dict[str, Any],
        summary_dict: Dict[str, Any],
        pdf_bytes: bytes,
        guard_enabled: bool,
        trace_id: str | None,
    ) -> Tuple[Dict[str, Any], Sequence[Tuple[str, str]]]:
        ocr_text = (ocr_result.get("text") or "").strip()
        pdf_pages = ocr_result.get("pages") or []
        doc_stats = {
            "pages": len(pdf_pages),
            "text_length": len(ocr_text),
            "file_size_mb": round(len(pdf_bytes) / (1024 * 1024), 3),
        }
        validation = self._supervisor.validate(
            ocr_text=ocr_text, summary=summary_dict, doc_stats=doc_stats
        )
        if not validation.get("supervisor_passed"):
            self._metrics.increment("supervisor_alerts", stage="supervisor")
            structured_log(
                _LOG,
                logging.WARNING,
                "supervisor_basic_check_failed",
                trace_id=trace_id,
                **validation,
            )
        sections = build_pdf_sections_from_payload(summary_dict)
        hits = _detect_forbidden_phrases(sections)
        if hits:
            self._metrics.increment(
                "pdf_validation_hits", stage="supervisor", amount=len(hits)
            )
            structured_log(
                _LOG,
                logging.WARNING,
                "pdf_validation_forbidden_phrases_detected",
                trace_id=trace_id,
                forbidden_phrases=hits,
                guard_enabled=guard_enabled,
            )
            if guard_enabled:
                self._metrics.increment("pdf_validation_blocks", stage="supervisor")
                raise PdfValidationError(
                    "PDF validation guard blocked forbidden phrases: "
                    + ", ".join(sorted(set(hits)))
                )
        validation["pdf_compliant"] = not hits
        if hits:
            validation["pdf_forbidden_phrases"] = hits
        return validation, sections

    def _build_pdf(
        self,
        *,
        title: str,
        sections: Sequence[Tuple[str, str]],
        trace_id: str | None,
        source: str,
    ) -> bytes:
        try:
            return self._pdf_writer.build(title, sections)
        except PDFGenerationError as exc:
            publish_pipeline_failure(
                stage="PDF_JOB",
                job_id=None,
                trace_id=trace_id,
                error=exc,
                metadata={"source": source},
            )
            structured_log(
                _LOG,
                logging.ERROR,
                "pdf_generation_failed",
                trace_id=trace_id,
                source=source,
                error=str(exc),
            )
            raise

    def _deliver_pdf(
        self, *, payload: bytes, trace_id: str | None, source: str
    ) -> str | None:
        write_to_drive = os.getenv("WRITE_TO_DRIVE", "true").strip().lower() == "true"
        if not write_to_drive:
            return None
        try:
            return self._pdf_delivery.deliver_pdf(
                payload,
                trace_id=trace_id,
                source=source,
                folder_id=self._drive_report_folder_id,
                metadata={"trace_id": trace_id, "source": source},
            )
        except DriveServiceError as exc:
            structured_log(
                _LOG,
                logging.ERROR,
                "drive_upload_failed",
                trace_id=trace_id,
                source=source,
                error=str(exc),
            )
            if self._stub_mode:
                return None
            raise


def _derive_title(
    ocr_result: Dict[str, Any], summary_dict: Dict[str, Any]
) -> str:  # pragma: no cover - trivial
    return (
        ocr_result.get("title")
        or summary_dict.get("title")
        or summary_dict.get("document_title")
        or "Medical Summary"
    )


def _detect_forbidden_phrases(
    sections: Sequence[Tuple[str, str]]
) -> list[str]:  # pragma: no cover - simple loop
    hits: list[str] = []
    for heading, body in sections:
        blob = f"{heading}\n{body}".lower()
        for phrase in FORBIDDEN_PDF_PHRASES:
            if phrase in blob:
                hits.append(phrase)
    return hits


__all__ = [
    "ProcessPipelineService",
    "ProcessPipelineResult",
    "PdfDeliveryService",
]
