"""Prepare summary input by triaging native PDF text before OCR."""

from __future__ import annotations

from dataclasses import dataclass
import io
import os
import re
from typing import Any, Dict, List, Mapping, Sequence

try:  # pragma: no cover - optional dependency in some runtimes
    from pypdf import PdfReader  # type: ignore
except Exception:  # pragma: no cover - allow OCR fallback when unavailable
    PdfReader = None  # type: ignore


_WHITESPACE_RE = re.compile(r"[ \t]+")


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


def _normalise_page_text(text: str) -> str:
    if not text:
        return ""
    lines = [
        _WHITESPACE_RE.sub(" ", line).strip()
        for line in str(text).replace("\r", "\n").splitlines()
    ]
    cleaned = "\n".join(line for line in lines if line)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


@dataclass(frozen=True)
class NativeTextTriageMetrics:
    page_count: int
    non_empty_pages: int
    non_empty_page_ratio: float
    total_chars: int
    avg_chars_per_page: float
    short_page_ratio: float
    alpha_ratio: float
    extractor_available: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page_count": self.page_count,
            "non_empty_pages": self.non_empty_pages,
            "non_empty_page_ratio": self.non_empty_page_ratio,
            "total_chars": self.total_chars,
            "avg_chars_per_page": self.avg_chars_per_page,
            "short_page_ratio": self.short_page_ratio,
            "alpha_ratio": self.alpha_ratio,
            "extractor_available": self.extractor_available,
        }


@dataclass(frozen=True)
class PreparedSummaryInput:
    requires_ocr: bool
    text_source: str
    route_reason: str
    text: str
    pages: List[Dict[str, Any]]
    triage_metrics: NativeTextTriageMetrics
    metadata_patch: Dict[str, Any]

    def to_payload(
        self, *, base_metadata: Mapping[str, Any] | None = None
    ) -> Dict[str, Any]:
        metadata = dict(base_metadata or {})
        metadata.update(self.metadata_patch)
        return {
            "text": self.text,
            "pages": list(self.pages),
            "metadata": metadata,
        }


def _extract_native_pages(pdf_bytes: bytes) -> tuple[List[Dict[str, Any]], bool]:
    if PdfReader is None:
        return [], False
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception:  # pragma: no cover - invalid/minimal PDFs fall back to OCR
        return [], True
    pages: List[Dict[str, Any]] = []
    for idx, page in enumerate(reader.pages, start=1):
        try:
            extracted = page.extract_text() or ""
        except Exception:  # pragma: no cover - per-page extraction best effort
            extracted = ""
        pages.append(
            {
                "page_number": idx,
                "text": _normalise_page_text(extracted),
            }
        )
    return pages, True


def _collect_native_text_metrics(
    pages: Sequence[Mapping[str, Any]], *, extractor_available: bool
) -> NativeTextTriageMetrics:
    page_texts = [str(page.get("text") or "") for page in pages]
    page_count = len(page_texts)
    non_empty_pages = sum(1 for text in page_texts if text.strip())
    total_chars = sum(len(text.strip()) for text in page_texts)
    avg_chars_per_page = round(total_chars / max(page_count, 1), 1)
    short_page_count = sum(1 for text in page_texts if len(text.strip()) < 80)
    alpha_chars = sum(sum(ch.isalpha() for ch in text) for text in page_texts)
    non_space_chars = sum(
        sum(not ch.isspace() for ch in text) for text in page_texts if text
    )
    return NativeTextTriageMetrics(
        page_count=page_count,
        non_empty_pages=non_empty_pages,
        non_empty_page_ratio=round(non_empty_pages / max(page_count, 1), 3),
        total_chars=total_chars,
        avg_chars_per_page=avg_chars_per_page,
        short_page_ratio=round(short_page_count / max(page_count, 1), 3),
        alpha_ratio=round(alpha_chars / max(non_space_chars, 1), 3),
        extractor_available=extractor_available,
    )


def _native_route_reason(metrics: NativeTextTriageMetrics) -> tuple[bool, str]:
    if not metrics.extractor_available:
        return True, "native_text_extractor_unavailable"

    min_chars = _int_env("SUMMARY_NATIVE_TEXT_MIN_CHARS", 200)
    min_page_ratio = _float_env("SUMMARY_NATIVE_TEXT_MIN_PAGE_RATIO", 0.5)
    min_avg_chars = _float_env("SUMMARY_NATIVE_TEXT_MIN_AVG_PAGE_CHARS", 80.0)
    min_alpha_ratio = _float_env("SUMMARY_NATIVE_TEXT_MIN_ALPHA_RATIO", 0.55)
    max_short_page_ratio = _float_env("SUMMARY_NATIVE_TEXT_MAX_SHORT_PAGE_RATIO", 0.8)

    if (
        metrics.page_count <= 0
        or metrics.total_chars < max(1, min_chars)
        or metrics.non_empty_pages <= 0
    ):
        return True, "scan_or_image_only"

    if metrics.non_empty_page_ratio < min_page_ratio:
        return True, "scan_or_image_only"

    if (
        metrics.avg_chars_per_page < min_avg_chars
        or metrics.alpha_ratio < min_alpha_ratio
        or metrics.short_page_ratio > max_short_page_ratio
    ):
        return True, "native_text_low_confidence"

    return False, "native_text_sufficient"


def prepare_summary_input_from_pdf_bytes(
    pdf_bytes: bytes,
    *,
    job_metadata: Mapping[str, Any] | None = None,
) -> PreparedSummaryInput:
    pages, extractor_available = _extract_native_pages(pdf_bytes)
    metrics = _collect_native_text_metrics(pages, extractor_available=extractor_available)
    requires_ocr, route_reason = _native_route_reason(metrics)
    prepared_pages = [dict(page) for page in pages if str(page.get("text") or "").strip()]
    prepared_text = "\n\n".join(
        str(page.get("text") or "").strip() for page in prepared_pages
    ).strip()
    text_source = "ocr" if requires_ocr else "native_text"
    metadata_patch: Dict[str, Any] = {
        "summary_text_source": text_source,
        "summary_requires_ocr": requires_ocr,
        "summary_triage_reason": route_reason,
        "summary_triage_metrics": metrics.to_dict(),
        "summary_native_text_available": bool(metrics.total_chars),
        "summary_fast_lane_default": True,
    }
    if isinstance(job_metadata, Mapping):
        for key in ("job_id", "object_uri", "document_id", "source"):
            value = job_metadata.get(key)
            if value:
                metadata_patch[key] = value

    return PreparedSummaryInput(
        requires_ocr=requires_ocr,
        text_source=text_source,
        route_reason=route_reason,
        text=prepared_text,
        pages=prepared_pages,
        triage_metrics=metrics,
        metadata_patch=metadata_patch,
    )


__all__ = [
    "NativeTextTriageMetrics",
    "PreparedSummaryInput",
    "prepare_summary_input_from_pdf_bytes",
]
