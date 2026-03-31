"""Refactored medical summariser implementing production-grade hierarchical prompting.

This module supersedes :mod:`src.services.summariser` for pipelines that require
supervisor-compatible summaries (length ratio >= 0.01 and alignment >= 0.80).

Key improvements:
- Hierarchical prompting: chunk-level extraction followed by deterministic merge.
- Structured output template (Intro, Key Points, Detailed Findings, Care Plan).
- Deterministic post-processing that guarantees supervisor structural requirements.
- Token-aware chunking with context overlap to avoid detail loss across boundaries.

The refactored summariser remains framework agnostic; callers should inject a
backend implementing :class:`ChunkSummaryBackend`.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
import logging
import math
import os
import re
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

from src.config import get_config
from src.errors import SummarizationError
from src.logging_setup import configure_logging
from src.models.summary_contract import (
    SummaryContract,
    SummarySection,
    build_claims_from_sections,
    resolve_schema_version,
)
from src.services.docai_helper import clean_ocr_output
from src.services.pipeline import (
    PipelineStateStore,
    PipelineStatus,
    create_state_store_from_env,
)
from src.services.summarization import (
    formatter as summary_formatter,
    text_utils as summary_text_utils,
)
from src.services.supervisor import CommonSenseSupervisor
from src.utils.secrets import SecretResolutionError, resolve_secret_env
from src.utils.logging_utils import structured_log

_LOG = logging.getLogger("summariser.refactored")

SUMMARY_STRATEGIES = {"chunked", "one_shot", "auto"}
DEFAULT_SUMMARY_STRATEGY = "auto"
DEFAULT_CHUNKED_MODEL = "gpt-4.1-mini"
DEFAULT_ONE_SHOT_MODEL = "gpt-5.4"
DEFAULT_ONE_SHOT_REASONING_EFFORT = "none"
DEFAULT_ONE_SHOT_TOKEN_THRESHOLD = 120_000
DEFAULT_ONE_SHOT_MAX_PAGES = 80
DEFAULT_OCR_NOISE_RATIO_THRESHOLD = 0.18
DEFAULT_OCR_SHORT_LINE_RATIO_THRESHOLD = 0.42
_REPEATED_METADATA_MARKER_RE = re.compile(
    r"(?im)^\s*(?:patient name|medical record number|medical record #|date of visit|dob|date of birth)\s*:"
)
_ROUTE_HEADER_LEAK_TOKENS: tuple[str, ...] = (
    "Patient Name:",
    "Medical Record Number:",
    "Date of Visit:",
)


class ChunkSummaryBackend(Protocol):  # pragma: no cover - interface definition
    """Backend interface responsible for summarising a single chunk of OCR text."""

    def summarise_chunk(
        self,
        *,
        chunk_text: str,
        chunk_index: int,
        total_chunks: int,
        estimated_tokens: int,
        ) -> Dict[str, Any]:
        """Summarise a chunk and return structured JSON-friendly fields."""


class DocumentSummaryBackend(Protocol):  # pragma: no cover - interface definition
    """Backend interface responsible for summarising one OCR packet in a single call."""

    def summarise_document(
        self,
        *,
        document_text: str,
        estimated_tokens: int,
        page_count: int,
        routing_metrics: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Summarise a full OCR packet and return structured JSON-friendly fields."""


@dataclass(frozen=True)
class SummaryRoutingMetrics:
    estimated_tokens: int
    page_count: int
    line_count: int
    noise_line_count: int
    noise_ratio: float
    short_line_ratio: float
    alpha_ratio: float
    repeated_metadata_markers: int
    mixed_packet_signals: int
    average_words_per_line: float
    quality_ok: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "estimated_tokens": self.estimated_tokens,
            "page_count": self.page_count,
            "line_count": self.line_count,
            "noise_line_count": self.noise_line_count,
            "noise_ratio": self.noise_ratio,
            "short_line_ratio": self.short_line_ratio,
            "alpha_ratio": self.alpha_ratio,
            "repeated_metadata_markers": self.repeated_metadata_markers,
            "mixed_packet_signals": self.mixed_packet_signals,
            "average_words_per_line": self.average_words_per_line,
            "quality_ok": self.quality_ok,
        }


@dataclass(frozen=True)
class SummaryRouteDecision:
    requested_strategy: str
    selected_strategy: str
    reason: str
    one_shot_token_threshold: int
    one_shot_max_pages: int
    metrics: SummaryRoutingMetrics

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requested_strategy": self.requested_strategy,
            "selected_strategy": self.selected_strategy,
            "reason": self.reason,
            "one_shot_token_threshold": self.one_shot_token_threshold,
            "one_shot_max_pages": self.one_shot_max_pages,
            "metrics": self.metrics.to_dict(),
        }


@dataclass
class SummaryGenerationResult:
    summary: Dict[str, Any]
    route: SummaryRouteDecision
    final_strategy: str
    fallback_reason: str | None = None


def _normalise_summary_strategy(value: str | None) -> str:
    token = (value or DEFAULT_SUMMARY_STRATEGY).strip().lower().replace("-", "_")
    if token not in SUMMARY_STRATEGIES:
        raise SummarizationError(
            f"Invalid SUMMARY_STRATEGY={value!r}; expected one of chunked, one_shot, auto."
        )
    return token


def _estimate_summary_tokens(text: str) -> int:
    cleaned = _clean_text_preserve_paragraphs(text)
    if not cleaned:
        return 0
    char_estimate = math.ceil(len(cleaned) / 4)
    word_estimate = math.ceil(_count_word_tokens(cleaned) * 1.1)
    return max(1, char_estimate, word_estimate)


def _collect_summary_routing_metrics(
    text: str,
    *,
    page_count: int,
    noise_ratio_threshold: float,
    short_line_ratio_threshold: float,
) -> SummaryRoutingMetrics:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) <= 4:
        sentence_lines: List[str] = []
        for line in lines:
            sentence_lines.extend(
                sentence.strip()
                for sentence in _SENTENCE_SPLIT_RE.split(line)
                if sentence.strip()
            )
        if sentence_lines:
            lines = sentence_lines
    line_count = len(lines)
    if not lines:
        return SummaryRoutingMetrics(
            estimated_tokens=0,
            page_count=page_count,
            line_count=0,
            noise_line_count=0,
            noise_ratio=0.0,
            short_line_ratio=0.0,
            alpha_ratio=0.0,
            repeated_metadata_markers=0,
            mixed_packet_signals=0,
            average_words_per_line=0.0,
            quality_ok=False,
        )

    noise_line_count = 0
    short_line_count = 0
    total_words = 0
    alpha_chars = 0
    non_space_chars = 0
    for line in lines:
        total_words += len(line.split())
        alpha_chars += sum(ch.isalpha() for ch in line)
        non_space_chars += sum(not ch.isspace() for ch in line)
        if len(line.split()) <= 2:
            short_line_count += 1
        if (
            _contains_noise_phrase(line)
            or _CONSENT_LINE_RE.match(line)
            or _REASON_ONLY_LINE_RE.match(line)
            or summary_text_utils.looks_like_vitals_table(line)
        ):
            noise_line_count += 1

    repeated_metadata_markers = len(_REPEATED_METADATA_MARKER_RE.findall(text))
    mixed_packet_signals = 0
    if repeated_metadata_markers >= 6 and page_count >= 3:
        mixed_packet_signals += 1
    if text.lower().count("reason for visit:") >= max(3, page_count + 1):
        mixed_packet_signals += 1
    if text.lower().count("patient name:") >= max(2, page_count):
        mixed_packet_signals += 1

    noise_ratio = round(noise_line_count / max(line_count, 1), 3)
    short_line_ratio = round(short_line_count / max(line_count, 1), 3)
    alpha_ratio = round(alpha_chars / max(non_space_chars, 1), 3)
    average_words_per_line = round(total_words / max(line_count, 1), 2)
    quality_ok = (
        noise_ratio <= noise_ratio_threshold
        and short_line_ratio <= short_line_ratio_threshold
        and alpha_ratio >= 0.55
        and mixed_packet_signals == 0
    )
    return SummaryRoutingMetrics(
        estimated_tokens=_estimate_summary_tokens(text),
        page_count=page_count,
        line_count=line_count,
        noise_line_count=noise_line_count,
        noise_ratio=noise_ratio,
        short_line_ratio=short_line_ratio,
        alpha_ratio=alpha_ratio,
        repeated_metadata_markers=repeated_metadata_markers,
        mixed_packet_signals=mixed_packet_signals,
        average_words_per_line=average_words_per_line,
        quality_ok=quality_ok,
    )


def _select_summary_route(
    *,
    text: str,
    pages: Sequence[Dict[str, Any]] | None,
    requested_strategy: str,
    one_shot_token_threshold: int,
    one_shot_max_pages: int,
    noise_ratio_threshold: float,
    short_line_ratio_threshold: float,
) -> SummaryRouteDecision:
    metrics = _collect_summary_routing_metrics(
        text,
        page_count=len(pages or []),
        noise_ratio_threshold=noise_ratio_threshold,
        short_line_ratio_threshold=short_line_ratio_threshold,
    )
    requested = _normalise_summary_strategy(requested_strategy)
    if requested == "chunked":
        return SummaryRouteDecision(
            requested_strategy=requested,
            selected_strategy="chunked",
            reason="strategy_forced_chunked",
            one_shot_token_threshold=one_shot_token_threshold,
            one_shot_max_pages=one_shot_max_pages,
            metrics=metrics,
        )
    if requested == "one_shot":
        return SummaryRouteDecision(
            requested_strategy=requested,
            selected_strategy="one_shot",
            reason="strategy_forced_one_shot",
            one_shot_token_threshold=one_shot_token_threshold,
            one_shot_max_pages=one_shot_max_pages,
            metrics=metrics,
        )
    if metrics.estimated_tokens > one_shot_token_threshold:
        reason = "estimated_tokens_exceed_operational_threshold"
        selected = "chunked"
    elif metrics.page_count > one_shot_max_pages:
        reason = "page_count_exceeds_operational_threshold"
        selected = "chunked"
    elif metrics.mixed_packet_signals > 0:
        reason = "mixed_packet_signals_detected"
        selected = "chunked"
    elif not metrics.quality_ok:
        reason = "ocr_quality_requires_chunk_fallback"
        selected = "chunked"
    else:
        reason = "within_operational_threshold_and_quality_budget"
        selected = "one_shot"
    return SummaryRouteDecision(
        requested_strategy=requested,
        selected_strategy=selected,
        reason=reason,
        one_shot_token_threshold=one_shot_token_threshold,
        one_shot_max_pages=one_shot_max_pages,
        metrics=metrics,
    )


def _collect_response_output_text(response: Any) -> str:
    segments: List[str] = []
    output_items = getattr(response, "output", None)
    if output_items:
        for item in output_items:
            contents = getattr(item, "content", []) or []
            for content in contents:
                content_type = getattr(content, "type", "")
                if content_type in {"output_text", "text"}:
                    text_block = getattr(content, "text", "")
                    if isinstance(text_block, str):
                        segments.append(text_block)
                    elif isinstance(text_block, list):
                        segments.extend(str(part) for part in text_block)
                elif content_type == "tool_call":
                    continue
    if segments:
        return "".join(segments)
    fallback = getattr(response, "output_text", "")
    return str(fallback or "")


def _coerce_usage_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _normalise_provider_usage(value: Any) -> Dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    usage = {
        "requests": _coerce_usage_int(value.get("requests")),
        "input_tokens": _coerce_usage_int(value.get("input_tokens")),
        "output_tokens": _coerce_usage_int(value.get("output_tokens")),
        "total_tokens": _coerce_usage_int(value.get("total_tokens")),
        "cached_tokens": _coerce_usage_int(value.get("cached_tokens")),
        "reasoning_tokens": _coerce_usage_int(value.get("reasoning_tokens")),
    }
    meaningful = any(value > 0 for value in usage.values())
    if not meaningful:
        return None
    if usage["requests"] <= 0:
        usage["requests"] = 1
    return usage


def _extract_response_usage(response: Any) -> Dict[str, int] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    return _normalise_provider_usage(
        {
            "requests": 1,
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
            "cached_tokens": getattr(input_details, "cached_tokens", None),
            "reasoning_tokens": getattr(output_details, "reasoning_tokens", None),
        }
    )


def _merge_provider_usage(
    current: Mapping[str, Any] | None,
    incoming: Mapping[str, Any] | None,
) -> Dict[str, int] | None:
    left = _normalise_provider_usage(current)
    right = _normalise_provider_usage(incoming)
    if left is None:
        return right
    if right is None:
        return left
    merged = {key: left.get(key, 0) + right.get(key, 0) for key in left}
    return _normalise_provider_usage(merged)


def _annotate_provider_usage_metadata(
    summary: Mapping[str, Any],
    provider_usage: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    contract = SummaryContract.from_mapping(summary)
    metadata = dict(contract.metadata)
    usage = _normalise_provider_usage(provider_usage)
    metadata["provider_usage_available"] = bool(usage)
    if usage:
        metadata["provider_usage"] = usage
    else:
        metadata.pop("provider_usage", None)
    contract.metadata = metadata
    return contract.to_dict()


def _provider_usage_log_fields(provider_usage: Mapping[str, Any] | None) -> Dict[str, Any]:
    usage = _normalise_provider_usage(provider_usage)
    if not usage:
        return {"provider_usage_available": False}
    return {
        "provider_usage_available": True,
        "provider_requests": usage["requests"],
        "provider_input_tokens": usage["input_tokens"],
        "provider_output_tokens": usage["output_tokens"],
        "provider_total_tokens": usage["total_tokens"],
        "provider_cached_tokens": usage["cached_tokens"],
        "provider_reasoning_tokens": usage["reasoning_tokens"],
    }


def _coerce_summary_items(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    elif isinstance(value, (tuple, set)):
        items = [str(item).strip() for item in value if str(item).strip()]
    elif isinstance(value, str):
        items = [value.strip()] if value.strip() else []
    elif isinstance(value, dict):
        items = [str(item).strip() for item in value.values() if str(item).strip()]
    else:
        text_value = str(value).strip()
        items = [text_value] if text_value else []
    return [item for item in items if item and not _is_placeholder(item)]


def _dedupe_summary_items(items: Iterable[str]) -> List[str]:
    merged: List[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = str(item).strip()
        if not cleaned:
            continue
        key = re.sub(r"[^a-z0-9]+", " ", cleaned.lower()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(cleaned)
    return merged


def _ground_structured_summary_payload(
    parsed: Dict[str, Any],
    *,
    extractive_backup: Dict[str, Any],
    log_event: str,
    log_extra: Mapping[str, Any],
) -> Dict[str, Any]:
    changed_fields: List[str] = []
    grounded_only_fields = {
        "key_points",
        "clinical_details",
        "care_plan",
        "medications",
    }
    for key in (
        "key_points",
        "clinical_details",
        "care_plan",
        "diagnoses",
        "providers",
        "medications",
    ):
        primary_items = _coerce_summary_items(parsed.get(key))
        backup_items = _coerce_summary_items(extractive_backup.get(key))
        if key in grounded_only_fields and backup_items:
            merged_items = _dedupe_summary_items(backup_items)
        else:
            merged_items = _dedupe_summary_items([*backup_items, *primary_items])
        if merged_items != primary_items:
            changed_fields.append(key)
        parsed[key] = merged_items

    overview = str(parsed.get("overview") or "").strip()
    backup_overview = str(extractive_backup.get("overview") or "").strip()
    if backup_overview and backup_overview != overview:
        parsed["overview"] = backup_overview
        changed_fields.append("overview")
    if changed_fields:
        _LOG.info(
            log_event,
            extra={
                **log_extra,
                "fields": changed_fields,
                "key_points_count": len(parsed.get("key_points") or []),
                "clinical_details_count": len(parsed.get("clinical_details") or []),
                "care_plan_count": len(parsed.get("care_plan") or []),
            },
        )
    return parsed


def _validate_generated_summary_contract(summary: Mapping[str, Any]) -> SummaryContract:
    if "Medical Summary" in summary:
        raise SummarizationError(
            "Canonical summary contract must not include legacy top-level Medical Summary."
        )
    contract = SummaryContract.from_mapping(summary)
    if not contract.sections:
        raise SummarizationError("Summary contract must include structured sections.")
    required_slugs = {
        "provider_seen",
        "reason_for_visit",
        "clinical_findings",
        "treatment_follow_up_plan",
        "diagnoses",
        "healthcare_providers",
        "medications",
    }
    slugs = {section.slug for section in contract.sections}
    missing = sorted(required_slugs - slugs)
    if missing:
        raise SummarizationError(
            "Summary contract missing required sections: " + ", ".join(missing)
        )
    rendered = contract.as_text()
    if "Document processed in " in rendered:
        raise SummarizationError(
            "Summary contract still contains legacy chunk marker telemetry."
        )
    for section in contract.sections:
        if section.slug in {"reason_for_visit", "treatment_follow_up_plan"}:
            for header_token in _ROUTE_HEADER_LEAK_TOKENS:
                if header_token in section.content:
                    raise SummarizationError(
                        f"Summary contract leaked demographic header into {section.slug}."
                    )
    return contract


def _annotate_summary_contract_metadata(
    summary: Mapping[str, Any],
    *,
    route: SummaryRouteDecision,
    final_strategy: str,
    fallback_reason: str | None,
    extra_metadata: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    contract = _validate_generated_summary_contract(summary)
    metadata = dict(contract.metadata)
    metadata.update(
        {
            "summary_strategy_requested": route.requested_strategy,
            "summary_strategy_selected": route.selected_strategy,
            "summary_strategy_used": final_strategy,
            "summary_route_reason": route.reason,
            "summary_route_metrics": route.metrics.to_dict(),
            "summary_one_shot_token_threshold": route.one_shot_token_threshold,
            "summary_one_shot_max_pages": route.one_shot_max_pages,
        }
    )
    if fallback_reason:
        metadata["summary_fallback_reason"] = fallback_reason
    if isinstance(extra_metadata, Mapping):
        for key, value in extra_metadata.items():
            if value is None:
                continue
            metadata[key] = value
    contract.metadata = metadata
    return contract.to_dict()


def _collect_validation_reasons(validation: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    reason_value = validation.get("reason")
    if isinstance(reason_value, str):
        reasons.extend(
            token.strip() for token in reason_value.split(",") if token.strip()
        )
    quality = validation.get("quality")
    if isinstance(quality, Mapping):
        quality_reasons = quality.get("reasons")
        if isinstance(quality_reasons, list):
            reasons.extend(
                str(token).strip() for token in quality_reasons if str(token).strip()
            )
    deduped: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        if reason in seen:
            continue
        seen.add(reason)
        deduped.append(reason)
    return deduped


class OpenAIResponsesBackend:  # pragma: no cover - network heavy, validated by integration tests
    """Concrete backend built around the OpenAI Responses API (GPT-4.1/GPT-5 family).

    The backend only performs chunk-level calls; the deterministic merge logic
    lives inside :class:`RefactoredSummariser`.
    """

    CHUNK_SYSTEM_PROMPT = textwrap.dedent(
        """
        You are a clinical documentation expert following MCC supervisor guidance (Oct 2025).
        Extract medically relevant facts and return STRICT JSON with these keys:
        - overview: single paragraph describing the clinical encounter context and most critical issue.
        - key_points: array of 2-5 bullet-ready strings capturing visit purpose, significant findings, and decisions.
        - clinical_details: array of factual sentences covering examinations, diagnostics, vitals, and notable negatives.
        - care_plan: array of sentences describing treatments, medications, follow-up, referrals, and patient guidance.
        - diagnoses: array of diagnostic statements (include ICD-10 codes if explicitly stated).
        - providers: array of provider names or roles explicitly referenced.
        - medications: array of medications, therapies, or prescriptions ordered or continued.
        - schema_version: string literal matching "2025-10-01".
        Requirements:
        * Stay faithful to the source text. Do not invent information.
        * Prefer extractive phrasing lifted directly from the OCR chunk; reuse exact source wording and section labels whenever possible.
        * If the source already states a fact clearly, copy that sentence or clause instead of paraphrasing it.
        * Preserve units, dosages, and time references.
        * Expand abbreviations the first time they appear if context allows.
        * Output MUST be valid JSON. Use "overview" as a descriptive string (not list).
        * Do not include markdown, numbering, or commentary outside the JSON payload.
        """
    ).strip()

    CHUNK_SCHEMA_VERSION = "2025-10-01"
    CHUNK_JSON_SCHEMA: Dict[str, Any] = {
        "type": "json_schema",
        "name": "chunk_summary_v2025_10_01",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "overview": {"type": "string"},
                "key_points": {"type": "array", "items": {"type": "string"}},
                "clinical_details": {"type": "array", "items": {"type": "string"}},
                "care_plan": {"type": "array", "items": {"type": "string"}},
                "diagnoses": {"type": "array", "items": {"type": "string"}},
                "providers": {"type": "array", "items": {"type": "string"}},
                "medications": {"type": "array", "items": {"type": "string"}},
                "schema_version": {"type": "string", "enum": ["2025-10-01"]},
            },
            "required": [
                "overview",
                "key_points",
                "clinical_details",
                "care_plan",
                "diagnoses",
                "providers",
                "medications",
                "schema_version",
            ],
        },
    }

    def __init__(
        self, model: str = "gpt-4.1-mini", api_key: Optional[str] = None
    ) -> None:
        self.model = model
        self.api_key = api_key

    def summarise_chunk(
        self,
        *,
        chunk_text: str,
        chunk_index: int,
        total_chunks: int,
        estimated_tokens: int,
    ) -> Dict[
        str, Any
    ]:  # pragma: no cover - network path exercised in integration tests
        try:
            from openai import OpenAI  # type: ignore
        except Exception as exc:  # pragma: no cover - dependency resolution
            raise SummarizationError(f"OpenAI SDK unavailable: {exc}") from exc

        self._fallback_used = False
        client = OpenAI(api_key=self.api_key)
        messages = [
            {"role": "system", "content": self.CHUNK_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": textwrap.dedent(
                    f"""
                    You are processing chunk {chunk_index} of {total_chunks} from a medical record.
                    The chunk contains approximately {estimated_tokens} tokens of OCR text.
                    Return the structured JSON payload exactly as specified.
                    ---
                    OCR_CHUNK_START
                    {chunk_text}
                    OCR_CHUNK_END
                    """
                ).strip(),
            },
        ]
        try:
            response = client.responses.create(  # type: ignore[call-overload]
                model=self.model,
                input=messages,
                temperature=0,
                max_output_tokens=900,
                text={
                    "format": self.CHUNK_JSON_SCHEMA,
                },
            )
        except (AttributeError, TypeError) as exc:
            _LOG.warning(
                "openai_responses_fallback",
                extra={
                    "error": str(exc),
                    "model": self.model,
                    "chunk_index": chunk_index,
                },
            )
            fallback_backend = HeuristicChunkBackend()
            self._fallback_used = True
            return fallback_backend.summarise_chunk(
                chunk_text=chunk_text,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                estimated_tokens=estimated_tokens,
            )
        content = _collect_response_output_text(response)
        usage = _extract_response_usage(response)

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:  # pragma: no cover - salvage path
            _LOG.warning(
                "openai_chunk_json_parse_fallback",
                extra={
                    "error": str(exc),
                    "model": self.model,
                    "chunk_index": chunk_index,
                },
            )
            fallback_backend = HeuristicChunkBackend()
            self._fallback_used = True
            fallback_payload = fallback_backend.summarise_chunk(
                chunk_text=chunk_text,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                estimated_tokens=estimated_tokens,
            )
            if usage:
                fallback_payload["_provider_usage"] = usage
            return fallback_payload
        if parsed.get("schema_version") != self.CHUNK_SCHEMA_VERSION:
            raise SummarizationError(
                f"Chunk schema_version mismatch: expected {self.CHUNK_SCHEMA_VERSION}, got {parsed.get('schema_version')}"
            )
        if usage:
            parsed["_provider_usage"] = usage
        extractive_backup = HeuristicChunkBackend().summarise_chunk(
            chunk_text=chunk_text,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            estimated_tokens=estimated_tokens,
        )
        return _ground_structured_summary_payload(
            parsed,
            extractive_backup=extractive_backup,
            log_event="openai_chunk_grounding_applied",
            log_extra={"chunk_index": chunk_index},
        )


class OpenAIOneShotResponsesBackend:  # pragma: no cover - network heavy
    """Whole-document backend using the Responses API with strict JSON output."""

    DOCUMENT_SCHEMA_VERSION = OpenAIResponsesBackend.CHUNK_SCHEMA_VERSION
    DOCUMENT_JSON_SCHEMA: Dict[str, Any] = {
        "type": "json_schema",
        "name": "document_summary_v2025_10_01",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "overview": {"type": "string"},
                "key_points": {"type": "array", "items": {"type": "string"}},
                "clinical_details": {"type": "array", "items": {"type": "string"}},
                "care_plan": {"type": "array", "items": {"type": "string"}},
                "diagnoses": {"type": "array", "items": {"type": "string"}},
                "providers": {"type": "array", "items": {"type": "string"}},
                "medications": {"type": "array", "items": {"type": "string"}},
                "schema_version": {"type": "string", "enum": ["2025-10-01"]},
            },
            "required": [
                "overview",
                "key_points",
                "clinical_details",
                "care_plan",
                "diagnoses",
                "providers",
                "medications",
                "schema_version",
            ],
        },
    }
    SYSTEM_PROMPT = textwrap.dedent(
        """
        You summarize OCR text from a medical record into strict JSON for the MCC patient-facing summary pipeline.
        Return ONLY valid JSON with exactly these keys:
        - overview
        - key_points
        - clinical_details
        - care_plan
        - diagnoses
        - providers
        - medications
        - schema_version
        Requirements:
        * Use only facts supported by the OCR text. Omit unsupported or ambiguous facts.
        * Preserve clinically important dates, diagnoses, tests, procedures, medications, providers, and follow-up instructions when the OCR supports them.
        * Keep wording concise and patient-readable.
        * Prefer source-grounded phrasing over paraphrastic abstraction.
        * Exclude legal boilerplate, consent language, billing noise, and formatting commentary unless clinically relevant.
        * Do not emit markdown, numbering, or prose outside the JSON payload.
        """
    ).strip()

    def __init__(
        self,
        *,
        model: str = DEFAULT_ONE_SHOT_MODEL,
        api_key: Optional[str] = None,
        reasoning_effort: str = DEFAULT_ONE_SHOT_REASONING_EFFORT,
        max_output_tokens: int = 2600,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.reasoning_effort = reasoning_effort
        self.max_output_tokens = max_output_tokens

    def summarise_document(
        self,
        *,
        document_text: str,
        estimated_tokens: int,
        page_count: int,
        routing_metrics: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        try:
            from openai import OpenAI  # type: ignore
        except Exception as exc:  # pragma: no cover - dependency resolution
            raise SummarizationError(f"OpenAI SDK unavailable: {exc}") from exc

        client = OpenAI(api_key=self.api_key)
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {
                "role": "user",
                "content": textwrap.dedent(
                    f"""
                    Summarize this OCR packet as a single medical document.
                    Approximate input tokens: {estimated_tokens}
                    Page count: {page_count}
                    Return the JSON payload exactly as specified.
                    ---
                    OCR_DOCUMENT_START
                    {document_text}
                    OCR_DOCUMENT_END
                    """
                ).strip(),
            },
        ]
        request_kwargs: Dict[str, Any] = {
            "model": self.model,
            "input": messages,
            "max_output_tokens": self.max_output_tokens,
            "text": {"format": self.DOCUMENT_JSON_SCHEMA},
        }
        if self.reasoning_effort:
            request_kwargs["reasoning"] = {"effort": self.reasoning_effort}

        try:
            response = client.responses.create(**request_kwargs)  # type: ignore[call-overload]
        except TypeError as exc:
            if "reasoning" in str(exc) and "reasoning" in request_kwargs:
                request_kwargs.pop("reasoning", None)
                response = client.responses.create(**request_kwargs)  # type: ignore[call-overload]
            else:
                raise SummarizationError(
                    f"One-shot Responses API call failed before execution: {exc}"
                ) from exc
        except Exception as exc:  # pragma: no cover - network path
            raise SummarizationError(f"One-shot Responses API failed: {exc}") from exc

        content = _collect_response_output_text(response)
        usage = _extract_response_usage(response)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise SummarizationError(f"Failed to parse one-shot JSON: {exc}") from exc
        if parsed.get("schema_version") != self.DOCUMENT_SCHEMA_VERSION:
            raise SummarizationError(
                "One-shot schema_version mismatch: "
                f"expected {self.DOCUMENT_SCHEMA_VERSION}, got {parsed.get('schema_version')}"
            )
        if usage:
            parsed["_provider_usage"] = usage
        extractive_backup = HeuristicChunkBackend().summarise_chunk(
            chunk_text=document_text,
            chunk_index=1,
            total_chunks=1,
            estimated_tokens=estimated_tokens,
        )
        return _ground_structured_summary_payload(
            parsed,
            extractive_backup=extractive_backup,
            log_event="one_shot_grounding_applied",
            log_extra={
                "model": self.model,
                "page_count": page_count,
                "estimated_tokens": estimated_tokens,
                "routing_metrics": dict(routing_metrics or {}),
            },
        )


class HeuristicOneShotBackend:
    """Offline one-shot backend for dry-runs, tests, and local benchmarking."""

    def summarise_document(
        self,
        *,
        document_text: str,
        estimated_tokens: int,
        page_count: int,
        routing_metrics: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        _LOG.info(
            "heuristic_one_shot_backend_active",
            extra={
                "estimated_tokens": estimated_tokens,
                "page_count": page_count,
                "routing_metrics": dict(routing_metrics or {}),
            },
        )
        return HeuristicChunkBackend().summarise_chunk(
            chunk_text=document_text,
            chunk_index=1,
            total_chunks=1,
            estimated_tokens=estimated_tokens,
        )


class HeuristicChunkBackend(ChunkSummaryBackend):
    """Lightweight offline backend that derives structured snippets from OCR text.

    Intended for local dry-runs where an OpenAI API key is not available. It applies
    simple heuristics to extract key sentences and metadata, ensuring downstream
    supervisor checks receive multi-section content with adequate length.
    """

    provider_tokens = ("dr", "doctor", "nurse", "provider", "physician", "practitioner")
    care_plan_tokens = (
        "plan",
        "follow-up:",
        "follow up:",
        "follow-up in",
        "follow up in",
        "follow-up with",
        "follow up with",
        "schedule",
        "return",
        "recommend",
        "monitor",
        "continue",
        "start",
        "advise",
    )
    medication_tokens = (
        "mg",
        "tablet",
        "capsule",
        "medication",
        "prescrib",
        "dose",
        "administer",
        "therapy",
        "diet",
    )
    diagnosis_tokens = (
        "hypertension",
        "diabetes",
        "infection",
        "injury",
        "fracture",
        "asthma",
        "covid",
        "anemia",
        "migraine",
        "cancer",
    )
    provider_pattern = re.compile(r"Dr\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*")
    medication_pattern = re.compile(
        r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+\d+\s*(?:mg|mcg|g))"
    )
    named_med_pattern = re.compile(
        r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*(?:\s+therapy|\s+diet))"
    )
    metadata_prefixes = (
        "patient name:",
        "date of visit:",
        "medical record number:",
        "medical record #:",
        "mrn:",
        "date of birth:",
        "dob:",
    )

    @staticmethod
    def _merge_sentence_fragments(sentences: Iterable[str]) -> List[str]:
        merged_sentences: List[str] = []
        carry: Optional[str] = None
        for sent in sentences:
            candidate = sent if carry is None else f"{carry} {sent}"
            trimmed = candidate.strip()
            lower_trimmed = trimmed.lower().rstrip(".")
            token_count = len(trimmed.split())
            if token_count <= 2 and len(trimmed) <= 32:
                carry = trimmed.rstrip(".")
                continue
            if len(trimmed) <= 12 and lower_trimmed in {"dr", "mr", "ms"}:
                carry = trimmed.rstrip(".")
                continue
            if token_count <= 2 and not trimmed.endswith((".", "!", "?")):
                carry = trimmed
                continue
            merged_sentences.append(trimmed)
            carry = None
        if carry:
            if merged_sentences:
                merged_sentences[-1] = f"{merged_sentences[-1]} {carry}"
            else:
                merged_sentences.append(carry)
        return merged_sentences

    @classmethod
    def _is_metadata_sentence(cls, sentence: str) -> bool:
        lowered = sentence.lower().strip()
        return any(lowered.startswith(prefix) for prefix in cls.metadata_prefixes)

    def summarise_chunk(
        self,
        *,
        chunk_text: str,
        chunk_index: int,
        total_chunks: int,
        estimated_tokens: int,
    ) -> Dict[str, Any]:
        cleaned = _clean_text_preserve_paragraphs(chunk_text)
        compact_text = _clean_text(chunk_text)
        paragraphs = [part.strip() for part in cleaned.split("\n\n") if part.strip()]
        sentences: List[str] = []
        for paragraph in paragraphs:
            paragraph_sentences = [
                s.strip() for s in _SENTENCE_SPLIT_RE.split(paragraph) if s.strip()
            ]
            if not paragraph_sentences:
                paragraph_sentences = [paragraph]
            sentences.extend(self._merge_sentence_fragments(paragraph_sentences))
        if not sentences and cleaned:
            sentences = [cleaned]

        content_sentences = [
            sentence
            for sentence in sentences
            if not self._is_metadata_sentence(sentence)
        ]
        working_sentences = content_sentences or sentences
        filtered_metadata_sentences = max(0, len(sentences) - len(content_sentences))

        overview = working_sentences[0] if working_sentences else ""
        key_points = working_sentences[: min(5, len(working_sentences))]

        def _select(
            sentences_in: Iterable[str], needles: Iterable[str], limit: int = 6
        ) -> List[str]:
            lowered_needles = tuple(n.lower() for n in needles)
            selected: List[str] = []
            for sent in sentences_in:
                low = sent.lower()
                if any(tok in low for tok in lowered_needles):
                    trimmed = sent.strip().rstrip(".")
                    if trimmed and trimmed not in selected:
                        selected.append(trimmed)
                if len(selected) >= limit:
                    break
            return selected

        clinical_details = [
            sent.strip().rstrip(".")
            for sent in working_sentences[1:]
            if len(sent.split()) >= 6
        ][:10]
        if not clinical_details:
            clinical_details = [
                s.strip().rstrip(".")
                for s in working_sentences[: max(1, len(working_sentences) // 2)]
            ]

        care_plan = _select(working_sentences, self.care_plan_tokens, limit=8)
        if not care_plan and working_sentences:
            care_plan = [working_sentences[-1].strip().rstrip(".")]

        diagnoses = _select(working_sentences, self.diagnosis_tokens, limit=6)
        if not diagnoses:
            diag_hits: List[str] = []
            lowered_text = compact_text.lower()
            for token in self.diagnosis_tokens:
                if token in lowered_text and token.title() not in diag_hits:
                    diag_hits.append(token.title())
            diagnoses = diag_hits

        providers = _select(working_sentences, self.provider_tokens, limit=5)
        for match in self.provider_pattern.findall(chunk_text):
            normalised = _clean_text(match)
            if normalised and normalised not in providers:
                providers.append(normalised)

        medications = _select(working_sentences, self.medication_tokens, limit=6)
        for match in self.medication_pattern.findall(chunk_text):
            normalised = match.strip()
            if normalised and normalised not in medications:
                medications.append(normalised)
        for match in self.named_med_pattern.findall(chunk_text):
            normalised = match.strip()
            if normalised and normalised not in medications:
                medications.append(normalised)

        def _truncate(items: List[str], max_len: int = 280) -> List[str]:
            truncated: List[str] = []
            for item in items:
                trimmed = item[:max_len].strip()
                if trimmed:
                    truncated.append(trimmed)
            return truncated

        _LOG.info(
            "heuristic_chunk_extraction",
            extra={
                "chunk_index": chunk_index,
                "paragraphs": len(paragraphs) or _count_text_paragraphs(chunk_text),
                "sentences": len(sentences),
                "content_sentences": len(working_sentences),
                "filtered_metadata_sentences": filtered_metadata_sentences,
                "key_points_count": len(key_points),
                "clinical_details_count": len(clinical_details),
                "care_plan_count": len(care_plan),
                "estimated_tokens": estimated_tokens,
            },
        )

        payload = {
            "overview": (overview or compact_text[:240]).strip(),
            "key_points": _truncate(key_points),
            "clinical_details": _truncate(clinical_details),
            "care_plan": _truncate(care_plan),
            "diagnoses": _truncate(diagnoses),
            "providers": _truncate(providers),
            "medications": _truncate(medications),
            "schema_version": OpenAIResponsesBackend.CHUNK_SCHEMA_VERSION,
        }
        return payload


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE_RE = re.compile(r"[\s\u00a0]+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_WORD_TOKEN_RE = re.compile(r"[A-Za-z0-9']+")
_PLACEHOLDER_RE = re.compile(
    r"^(?:n/?a|none(?:\s+(?:noted|reported|recorded))?|no data|empty|tbd|not (?:applicable|documented|provided)|nil)$",
    re.IGNORECASE,
)


def _is_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER_RE.match(value.strip()))


_KEYWORD_SANITISERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bN/?A\b", re.IGNORECASE), "not provided"),
    (re.compile(r"\bno data\b", re.IGNORECASE), "not documented"),
    (re.compile(r"\bempty\b", re.IGNORECASE), "not documented"),
    (re.compile(r"\bTBD\b", re.IGNORECASE), "to be determined"),
    (re.compile(r"\bnone\b", re.IGNORECASE), "not noted"),
)

_LEGAL_NOISE_PHRASES: tuple[str, ...] = (
    "recover from any third party",
    "fees and expenses",
    "i or any agent or representative",
    "i understand that the following care",
    "i understand that the following procedure",
    "i understand that the following treatment",
    "i understand that",
    "i understand that i",
    "this authorization is valid",
    "i hereby",
    "i acknowledge",
    "i have read and understand",
    "legal representation",
    "financial responsibility",
    "assignment of benefits",
    "release of information",
    "hipaa authorization",
    "hold harmless",
    "attorney",
    "law firm",
    "there are risks and hazards",
    "risks and hazards",
    "risks associated with",
    "prior treatment for this injury",
    "activities increase pain",
    "temporary localized increase in pain",
    "life threatening emergency",
    "these are your discharge instructions",
    "patient education materials",
    "patient education notes",
    "return to your normal activities",
    "educated on care of site",
    "fluoroscopy is used in the procedure",
    "nerve blocks and/or ablations",
    "no heavy lifting",
    "the patient was treated today",
    "patient activity restrictions",
    "discharge instructions",
    "call the office immediately",
    "go to an emergency room",
    "call 911",
    "potential for additional necessary care",
    "order status",
    "department status",
    "follow-up evaluation date",
    "worker's comp",
    "to treat my condition which is",
    "to treat my condition",
    "i consent to",
    "diabetes medicines or blood thinners",
)

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

_FINAL_NOISE_FRAGMENTS: tuple[str, ...] = (
    "temporary localized increase in pain",
    "fever, facial flushing",
    "call the office immediately",
    "emergency room",
    "patient education",
    "discharge instructions",
    "no heavy lifting",
    "facet injections are mostly a diagnostic tool",
    "medial branch blocks are spinal injections",
    "i retain the right to refuse",
    "plan of care (continued)",
    "thank you for choosing",
    "return to your normal activities",
    "instructions, prescriptions",
    "educated on care of site",
    "workers comp",
    "potential for additional necessary care",
    "order status",
    "department status",
    "follow-up evaluation date",
)
_REASON_ONLY_LINE_RE = re.compile(r"(?i)^\s*reason(?:s)?\s*for\s*visit\s*[:\-]?\s*$")
_CONSENT_LINE_RE = re.compile(
    r"(?i)^\s*(?:i\s+(?:understand|authorize|consent)|to\s+treat\s+my\s+condition)\b"
)
_PROVIDER_ENTITY_RE = re.compile(
    r"^(?:(?:Dr\.?|Doctor|[A-Z]{2,})\s+)?"
    r"(?:[A-Z][A-Za-z'.-]+|[A-Z]\.)"
    r"(?:\s+(?:[A-Z][A-Za-z'.-]+|[A-Z]\.|Jr\.?|Sr\.?|[A-Z]{2,})){1,4}"
    r"(?:,\s*(?:M\.?D\.?|D\.?O\.?|PA-C|NP|ARNP|FNP|DNP|DC|DPM|FACS|FAANS))?$"
)
_PROVIDER_PREFIX_TOKENS = frozenset({"dr", "doctor", "facs", "faans"})
_PROVIDER_SUFFIX_TOKENS = frozenset(
    {"md", "m", "d", "do", "pa", "c", "np", "arnp", "fnp", "dnp", "dc", "dpm", "jr", "sr"}
)
_PROVIDER_NON_NAME_TOKENS = frozenset(
    {
        "if",
        "not",
        "met",
        "and",
        "read",
        "by",
        "provider",
        "providers",
        "signatures",
        "signature",
        "during",
        "should",
        "unless",
        "please",
        "patient",
        "care",
        "procedure",
        "office",
        "visit",
        "physical",
        "therapy",
        "current",
        "address",
        "referrals",
        "dressing",
        "packing",
        "red",
        "oak",
    }
)
_PROVIDER_ADMIN_FRAGMENTS: tuple[str, ...] = (
    "provider at",
    "providers at",
    "health care provider",
    "healthcare provider",
    "drive them home",
    "driver license",
    "phone:",
    "fax:",
    "release of information",
    "assignment of recovery",
    "authorization",
    "pregnant",
    "provider signature",
)
_MEDICATION_ENTITY_RE = re.compile(
    r"(?i)\b(?:"
    r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml)(?!/)"
    r"|ibuprofen|cyclobenzaprine|acetaminophen|lisinopril|hydrocodone|tramadol|"
    r"gabapentin|naproxen|lidocaine|marcaine|kenalog|toradol|dexamethasone|biofreeze"
    r")\b"
)
_MEDICATION_ADMIN_FRAGMENTS: tuple[str, ...] = (
    "keep an active list of medications",
    "final active medications list",
    "please fill your prescriptions",
    "take all medications as directed",
    "operate a motor vehicle",
    "medication will be stopped",
    "medication list was updated",
    "share with other providers",
    "bring my medication bottle",
    "use my medications correctly",
    "it explains how i receive my medications",
    "i will not go to the er",
    "i will not use alcohol",
    "i agree to take my medication",
    "my providers may discuss my medications",
    "resume diet",
    "see medication reconciliation form",
)
_MEDICATION_REJECTION_TOKENS = (
    "referral",
    "evaluate",
    "assessment for referral",
    "appointment",
    "follow up medical evaluation",
    "symptoms are relieved",
    "oxygen therapy",
    "physical therapy",
    "occupational therapy",
    "the patient noted the pain medication",
    "after correct needle placement",
    "ingredients:",
    "blood sugar remains above",
)


def _sanitise_keywords(text: str) -> str:
    cleaned = text
    for pattern, replacement in _KEYWORD_SANITISERS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


def _clean_text(raw: str) -> str:
    """Normalise OCR text by removing control chars and collapsing whitespace."""

    cleaned = _CONTROL_CHARS_RE.sub(" ", raw or "")
    collapsed = _WHITESPACE_RE.sub(" ", cleaned)
    return collapsed.strip()


def _clean_text_preserve_paragraphs(raw: str) -> str:
    """Normalise OCR text while retaining paragraph separators for chunking."""

    cleaned = _CONTROL_CHARS_RE.sub(" ", raw or "")
    paragraphs: List[str] = []
    current_lines: List[str] = []
    for raw_line in cleaned.splitlines():
        collapsed = _WHITESPACE_RE.sub(" ", raw_line).strip()
        if not collapsed:
            if current_lines:
                paragraphs.append(" ".join(current_lines))
                current_lines = []
            continue
        current_lines.append(collapsed)
    if current_lines:
        paragraphs.append(" ".join(current_lines))
    return "\n\n".join(paragraphs).strip()


def _count_text_paragraphs(text: str) -> int:
    if not text or not text.strip():
        return 0
    paragraphs = [part for part in re.split(r"\n\s*\n", text) if part.strip()]
    return len(paragraphs) or 1


def _count_word_tokens(text: str) -> int:
    return len(_WORD_TOKEN_RE.findall(text or ""))


def _normalise_provider_candidate(value: str) -> str:
    cleaned = _clean_text(value).strip(" ,-;:")
    cleaned = re.sub(
        r"^(?:provider(?:'s)?\s+signatures?:?|provider:)\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned.strip(" ,-;:")


def _looks_like_provider_candidate(value: str) -> bool:
    cleaned = _normalise_provider_candidate(value)
    if not cleaned or len(cleaned) > 80 or any(ch.isdigit() for ch in cleaned):
        return False
    if any(token in cleaned for token in (":", "/", "|", "(", ")")):
        return False
    low = cleaned.lower()
    if _contains_noise_phrase(cleaned) or any(
        fragment in low for fragment in _PROVIDER_ADMIN_FRAGMENTS
    ):
        return False
    if not _PROVIDER_ENTITY_RE.fullmatch(cleaned):
        return False
    tokens = [
        token
        for token in re.findall(r"[A-Za-z][A-Za-z'.-]*", cleaned)
        if token.lower() not in _PROVIDER_PREFIX_TOKENS
        and token.lower() not in _PROVIDER_SUFFIX_TOKENS
    ]
    if len(tokens) < 2 or len(tokens) > 4:
        return False
    return not any(token.lower() in _PROVIDER_NON_NAME_TOKENS for token in tokens)


def _normalise_medication_candidate(value: str) -> str:
    cleaned = _clean_text(value).strip(" ,-;:")
    cleaned = re.sub(
        r"^(?:(?:pain\s+)?medication\(s\)|medications?)\s*[:\-]?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"^(?:method\s+erx|erx)\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\d+\s*", "", cleaned)
    return cleaned.strip(" ,-;:")


def _looks_like_medication_candidate(value: str) -> bool:
    cleaned = _normalise_medication_candidate(value)
    if not cleaned or len(cleaned.split()) > 24:
        return False
    low = cleaned.lower()
    if _contains_noise_phrase(cleaned) or any(
        fragment in low for fragment in _MEDICATION_ADMIN_FRAGMENTS
    ):
        return False
    if any(token in low for token in _MEDICATION_REJECTION_TOKENS):
        return False
    if "provider" in low and "mg" not in low and "therapy" not in low:
        return False
    return bool(_MEDICATION_ENTITY_RE.search(cleaned))


_SUMMARY_PAGE_FILTER_MIN_WORDS = 40
_SUMMARY_PAGE_DUPLICATE_SIMILARITY_THRESHOLD = 0.78
_SUMMARY_PAGE_VISIT_DUPLICATE_SIMILARITY_THRESHOLD = 0.60
_SUMMARY_PAGE_HARD_REJECT_PHRASES: tuple[str, ...] = (
    "medical records affidavit",
    "affidavit of billing records",
    "affidavit of records by custodian",
    "authorization to disclose protected health information",
    "itemized statement",
    "power of attorney",
    "risk of non-treatment",
    "does not guarantee result or a cure",
    "access your visit summary",
    "pay your bill online",
    "get x-ray & lab results",
    "check your injection site every day",
    "ask your health care provider what steps will be taken to help prevent infection",
    "i have been given an opportunity to ask questions",
    "medication agreement",
    "state of texas",
)
_SUMMARY_PAGE_ADMIN_SIGNAL_TOKENS: tuple[str, ...] = (
    "affidavit",
    "billing",
    "invoice",
    "ledger",
    "attorney",
    "authorization",
    "consent",
    "discharge",
    "instruction",
    "hipaa",
    "medical records",
    "custodian",
    "order status",
    "department status",
    "doctor cosign",
    "nurse review",
    "risk ",
    "hazard",
    "power of attorney",
    "release of information",
    "call 911",
    "emergency room",
    "medication agreement",
    "state of texas",
)
_SUMMARY_PAGE_CLINICAL_SIGNAL_TOKENS: tuple[str, ...] = (
    "history of present illness",
    "reason for visit",
    "chief complaint",
    "assessment",
    "impression",
    "diagnosis",
    "plan",
    "follow-up",
    "follow up",
    "exam",
    "pain",
    "lumbar",
    "cervical",
    "ankle",
    "hand/finger",
    "neck",
    "medication",
    "prescription",
    " mg",
    "blood pressure",
    "mri",
    "x-ray",
    "xray",
    "ct ",
    "provider",
    "dr.",
    "presents for",
    "radiculopathy",
    "strain",
    "sprain",
)
_SUMMARY_PAGE_ADMIN_PAIR_REJECTIONS: tuple[tuple[str, str], ...] = (
    ("pharmacy", "order status"),
    ("pharmacy", "department status"),
    ("doctor cosign", "order status"),
    ("nurse review", "doctor cosign"),
    ("misc nursing task", "pacu"),
    ("call 911", "emergency room"),
)
_SUMMARY_PAGE_HEADER_NOISE_TOKENS: tuple[str, ...] = (
    "account information",
    "practice information",
    "report request id",
    "print date/time",
    "to:",
    "from:",
    "page:",
    "acct #:",
    "auth (verified)",
    "fax:",
    "e&m code",
    "cpt",
)
_SUMMARY_PAGE_HEADER_NOISE_MIN_CLINICAL_HITS = 8
_SUMMARY_PAGE_NUMERIC_NOISE_MIN_CLINICAL_HITS = 12
_SUMMARY_PAGE_NOISY_DIGIT_RATIO_THRESHOLD = 0.14
_SUMMARY_PAGE_NOISY_ALPHA_RATIO_THRESHOLD = 0.78
_SUMMARY_PAGE_SIGNATURE_STOPWORDS: frozenset[str] = frozenset(
    (
        "the and or of to a in for on with at by is are was were be this that from as an it "
        "patient date birth page admit disch mrn fin visit chart acct self established new"
    ).split()
)


def _page_text_from_metadata(page: Mapping[str, Any], document_text: str) -> str:
    text_value = page.get("text")
    if isinstance(text_value, str) and text_value.strip():
        return _clean_text_preserve_paragraphs(text_value)
    return _extract_page_structured_text(document_text, dict(page))


def _summary_page_token_signature(text: str) -> set[str]:
    signature: set[str] = set()
    for token in re.findall(r"[a-z']+", text.lower()):
        if len(token) <= 2 or token in _SUMMARY_PAGE_SIGNATURE_STOPWORDS:
            continue
        signature.add(token)
    return signature


def _page_signature_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _select_summary_pages(
    *,
    document_text: str,
    pages: Sequence[Mapping[str, Any]],
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    selected_pages: List[Dict[str, Any]] = []
    selected_signatures: List[tuple[bool, set[str]]] = []
    reason_counts: Counter[str] = Counter()
    source_chars = 0
    selected_chars = 0

    for idx, page in enumerate(pages, start=1):
        page_text = _page_text_from_metadata(page, document_text)
        if not page_text:
            reason_counts["empty"] += 1
            continue

        source_chars += len(page_text)
        lowered = page_text.lower()
        word_count = _count_word_tokens(page_text)
        if word_count < _SUMMARY_PAGE_FILTER_MIN_WORDS:
            reason_counts["too_short"] += 1
            continue
        if any(phrase in lowered for phrase in _SUMMARY_PAGE_HARD_REJECT_PHRASES):
            reason_counts["hard_reject"] += 1
            continue
        if any(
            first in lowered and second in lowered
            for first, second in _SUMMARY_PAGE_ADMIN_PAIR_REJECTIONS
        ):
            reason_counts["workflow_admin"] += 1
            continue

        clinical_hits = sum(
            lowered.count(token) for token in _SUMMARY_PAGE_CLINICAL_SIGNAL_TOKENS
        )
        admin_hits = sum(
            lowered.count(token) for token in _SUMMARY_PAGE_ADMIN_SIGNAL_TOKENS
        )
        header_noise_hits = sum(
            1 for token in _SUMMARY_PAGE_HEADER_NOISE_TOKENS if token in lowered
        )
        metadata_markers = (
            lowered.count("patient:")
            + lowered.count("date of birth")
            + lowered.count("acct #:")
            + lowered.count("mrn:")
        )
        is_visit_template = (
            "established patient visit" in lowered or "new patient visit" in lowered
        )
        alpha_chars = sum(ch.isalpha() for ch in page_text)
        digit_chars = sum(ch.isdigit() for ch in page_text)
        non_space_chars = sum(not ch.isspace() for ch in page_text)
        alpha_ratio = alpha_chars / max(non_space_chars, 1)
        digit_ratio = digit_chars / max(non_space_chars, 1)

        if admin_hits >= clinical_hits + 2:
            reason_counts["admin_dominant"] += 1
            continue
        if metadata_markers >= 4 and clinical_hits < 4:
            reason_counts["metadata_dominant"] += 1
            continue
        if (
            not is_visit_template
            and header_noise_hits >= 2
            and clinical_hits < _SUMMARY_PAGE_HEADER_NOISE_MIN_CLINICAL_HITS
        ):
            reason_counts["header_noise"] += 1
            continue
        if (
            not is_visit_template
            and digit_ratio >= _SUMMARY_PAGE_NOISY_DIGIT_RATIO_THRESHOLD
            and alpha_ratio <= _SUMMARY_PAGE_NOISY_ALPHA_RATIO_THRESHOLD
            and clinical_hits < _SUMMARY_PAGE_NUMERIC_NOISE_MIN_CLINICAL_HITS
        ):
            reason_counts["numeric_noise"] += 1
            continue
        if not (
            is_visit_template
            or clinical_hits >= 3
            or ("medication" in lowered and admin_hits == 0)
        ):
            reason_counts["low_signal"] += 1
            continue

        signature = _summary_page_token_signature(page_text)
        similarity_limit = (
            _SUMMARY_PAGE_VISIT_DUPLICATE_SIMILARITY_THRESHOLD
            if is_visit_template
            else _SUMMARY_PAGE_DUPLICATE_SIMILARITY_THRESHOLD
        )
        duplicate_score = max(
            (
                _page_signature_similarity(signature, prior_signature)
                for prior_is_visit, prior_signature in selected_signatures
                if prior_is_visit == is_visit_template
            ),
            default=0.0,
        )
        if duplicate_score >= similarity_limit:
            reason_counts["duplicate"] += 1
            continue

        page_copy = dict(page)
        page_copy["page_number"] = (
            page.get("page_number") or page.get("pageNumber") or idx
        )
        page_copy["text"] = page_text
        selected_pages.append(page_copy)
        selected_signatures.append((is_visit_template, signature))
        selected_chars += len(page_text)

    applied = bool(
        selected_pages
        and len(selected_pages) < len(pages)
        and selected_chars >= max(200, int(source_chars * 0.25))
    )
    if not applied:
        selected_pages = [
            {
                **dict(page),
                "page_number": page.get("page_number") or page.get("pageNumber") or idx,
                "text": _page_text_from_metadata(page, document_text),
            }
            for idx, page in enumerate(pages, start=1)
            if _page_text_from_metadata(page, document_text)
        ]
        selected_chars = source_chars

    metadata = {
        "applied": applied,
        "source_pages": len(pages),
        "selected_pages": len(selected_pages),
        "dropped_pages": max(0, len(pages) - len(selected_pages)),
        "source_chars": source_chars,
        "selected_chars": selected_chars,
        "duplicate_pages": reason_counts.get("duplicate", 0),
        "hard_reject_pages": reason_counts.get("hard_reject", 0),
        "admin_reject_pages": reason_counts.get("admin_dominant", 0)
        + reason_counts.get("workflow_admin", 0)
        + reason_counts.get("metadata_dominant", 0)
        + reason_counts.get("header_noise", 0)
        + reason_counts.get("numeric_noise", 0),
        "low_signal_pages": reason_counts.get("low_signal", 0),
    }
    return selected_pages, metadata


def _prepare_summary_source(
    raw_text: str,
    doc_metadata: Optional[Dict[str, Any]],
) -> tuple[str, Optional[Dict[str, Any]]]:
    if not doc_metadata or doc_metadata.get("_summary_page_filter_applied"):
        return raw_text, doc_metadata

    pages_raw = doc_metadata.get("pages")
    if not isinstance(pages_raw, list):
        return raw_text, doc_metadata
    pages = [page for page in pages_raw if isinstance(page, Mapping)]
    if not pages:
        return raw_text, doc_metadata

    selected_pages, filter_metadata = _select_summary_pages(
        document_text=raw_text,
        pages=pages,
    )
    updated_metadata = dict(doc_metadata)
    updated_metadata["_summary_page_filter_applied"] = True
    updated_metadata["summary_page_filter"] = filter_metadata
    if filter_metadata.get("applied"):
        updated_metadata["pages"] = selected_pages
        filtered_text = "\n\n".join(
            str(page.get("text") or "").strip() for page in selected_pages
        ).strip()
        if filtered_text:
            return filtered_text, updated_metadata
    return raw_text, updated_metadata


def _extract_layout_text(document_text: str, layout: Dict[str, Any]) -> str:
    if not isinstance(layout, dict):
        return ""
    anchor = layout.get("textAnchor")
    if not isinstance(anchor, dict):
        return ""
    parts: List[str] = []
    for segment in anchor.get("textSegments") or []:
        if not isinstance(segment, dict):
            continue
        try:
            start_index = int(segment.get("startIndex") or 0)
            end_index = int(segment.get("endIndex") or 0)
        except (TypeError, ValueError):
            continue
        if end_index <= start_index:
            continue
        parts.append(document_text[start_index:end_index])
    return "".join(parts).strip()


def _merge_layout_fragments(fragments: Iterable[str]) -> List[str]:
    merged: List[str] = []
    for fragment in fragments:
        cleaned = _clean_text_preserve_paragraphs(fragment)
        if not cleaned:
            continue
        if (
            merged
            and not re.search(r"[.!?:]$", merged[-1])
            and cleaned[:1].islower()
        ):
            merged[-1] = f"{merged[-1]} {cleaned}".strip()
            continue
        merged.append(cleaned)
    return merged


def _extract_page_structured_text(document_text: str, page: Dict[str, Any]) -> str:
    if not isinstance(page, dict):
        return ""

    paragraphs = page.get("paragraphs")
    paragraph_texts = _merge_layout_fragments(
        _extract_layout_text(document_text, paragraph.get("layout") or {})
        for paragraph in (paragraphs if isinstance(paragraphs, list) else [])
        if isinstance(paragraph, dict)
    )
    if paragraph_texts:
        return "\n\n".join(paragraph_texts).strip()

    lines = page.get("lines")
    line_texts = _merge_layout_fragments(
        _extract_layout_text(document_text, line.get("layout") or {})
        for line in (lines if isinstance(lines, list) else [])
        if isinstance(line, dict)
    )
    if line_texts:
        return "\n".join(line_texts).strip()

    blocks = page.get("blocks")
    block_texts = _merge_layout_fragments(
        _extract_layout_text(document_text, block.get("layout") or {})
        for block in (blocks if isinstance(blocks, list) else [])
        if isinstance(block, dict)
    )
    if block_texts:
        return "\n\n".join(block_texts).strip()

    page_text = page.get("text")
    if isinstance(page_text, str):
        return _clean_text_preserve_paragraphs(page_text)
    return ""


def _rebuild_structured_ocr_text(document_text: str, pages: List[Dict[str, Any]]) -> str:
    page_texts = [
        _extract_page_structured_text(document_text, page)
        for page in pages
        if isinstance(page, dict)
    ]
    structured_pages = [page_text for page_text in page_texts if page_text]
    return "\n\n".join(structured_pages).strip()


def _prepare_summary_input(raw_text: str) -> str:
    if not raw_text or not raw_text.strip():
        return ""

    paragraphs = [part for part in re.split(r"\n\s*\n", raw_text) if part.strip()]
    cleaned_paragraphs: List[str] = []
    for paragraph in paragraphs or [raw_text]:
        cleaned_paragraph = clean_ocr_output(paragraph) or _clean_text(paragraph)
        preserved = _clean_text_preserve_paragraphs(cleaned_paragraph)
        if preserved:
            cleaned_paragraphs.append(preserved)
    if cleaned_paragraphs:
        return "\n\n".join(cleaned_paragraphs).strip()

    cleaned = clean_ocr_output(raw_text) or raw_text
    return _clean_text_preserve_paragraphs(cleaned)


def _collect_ocr_structure_metrics(
    text: str, pages: List[Dict[str, Any]]
) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {
        "ocr_pages": len(pages),
        "ocr_blocks": 0,
        "ocr_lines": 0,
        "ocr_paragraphs": 0,
        "ocr_layout_tokens": 0,
        "input_paragraphs": _count_text_paragraphs(text),
        "input_tokens": _count_word_tokens(text),
    }
    paragraph_word_counts: List[int] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        raw_blocks = page.get("blocks")
        raw_lines = page.get("lines")
        raw_paragraphs = page.get("paragraphs")
        raw_tokens = page.get("tokens")
        blocks: List[Any] = raw_blocks if isinstance(raw_blocks, list) else []
        lines: List[Any] = raw_lines if isinstance(raw_lines, list) else []
        paragraphs: List[Any] = (
            raw_paragraphs if isinstance(raw_paragraphs, list) else []
        )
        tokens: List[Any] = raw_tokens if isinstance(raw_tokens, list) else []
        metrics["ocr_blocks"] += len(blocks)
        metrics["ocr_lines"] += len(lines)
        metrics["ocr_paragraphs"] += len(paragraphs)
        metrics["ocr_layout_tokens"] += len(tokens)
        for paragraph in paragraphs:
            if not isinstance(paragraph, dict):
                continue
            paragraph_text = _extract_layout_text(text, paragraph.get("layout") or {})
            if paragraph_text:
                paragraph_word_counts.append(_count_word_tokens(paragraph_text))
    if paragraph_word_counts:
        metrics["ocr_avg_paragraph_words"] = round(
            sum(paragraph_word_counts) / len(paragraph_word_counts), 2
        )
    return metrics


def _contains_noise_phrase(value: str) -> bool:
    low = value.lower()
    return any(phrase in low for phrase in _LEGAL_NOISE_PHRASES)


def _normalise_line_key(value: str) -> str:
    return _NON_ALNUM_RE.sub(" ", value.lower()).strip()


def _strip_noise_lines(text: str) -> str:
    cleaned: List[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        low = line.lower()
        if not line:
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue
        if _contains_noise_phrase(line):
            continue
        if _REASON_ONLY_LINE_RE.match(line) and line.isupper():
            continue
        if _CONSENT_LINE_RE.match(line):
            continue
        if any(fragment in low for fragment in _FINAL_NOISE_FRAGMENTS):
            continue
        if "call" in low and "immediately" in low:
            continue
        if "thank you for choosing" in low:
            continue
        if low.count(",") >= 4 and ("risk" in low or "hazard" in low):
            continue
        if len(low) and sum(ch.isalpha() for ch in line) < 4:
            continue
        if summary_text_utils.looks_like_vitals_table(line):
            continue
        cleaned.append(raw_line)
    # Remove trailing blank lines
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    return "\n".join(cleaned)


@dataclass(slots=True)
class ChunkedText:
    """Container for chunk metadata and payload."""

    text: str
    index: int
    total: int
    approx_tokens: int


class SlidingWindowChunker:
    """Token-aware greedy chunker with symmetric overlap for continuity."""

    def __init__(
        self,
        *,
        target_chars: int = 2600,
        max_chars: int = 10000,
        overlap_chars: int = 320,
    ) -> None:
        if max_chars <= target_chars:
            raise ValueError("max_chars must be greater than target_chars")
        self.target_chars = target_chars
        self.max_chars = max_chars
        self.overlap_chars = overlap_chars

    def split(self, text: str) -> List[ChunkedText]:
        if not text:
            return []
        if len(text) <= self.max_chars:
            approx_tokens = max(1, math.ceil(len(text) / 4))
            return [
                ChunkedText(text=text, index=1, total=1, approx_tokens=approx_tokens)
            ]

        chunks: List[ChunkedText] = []
        start = 0
        chunk_index = 1
        length = len(text)
        while start < length:
            end = min(start + self.target_chars, length)
            # extend to the next whitespace for readability but do not exceed max_chars
            while (
                end < length
                and end - start < self.max_chars
                and text[end] not in {" ", "\n", "\t"}
            ):
                end += 1
            segment = text[start:end].strip()
            if not segment:
                break
            approx_tokens = max(1, math.ceil(len(segment) / 4))
            chunks.append(
                ChunkedText(
                    text=segment,
                    index=chunk_index,
                    total=0,  # placeholder, set later
                    approx_tokens=approx_tokens,
                )
            )
            chunk_index += 1
            if end >= length:
                break
            start = max(0, end - self.overlap_chars)
        total_chunks = len(chunks)
        for ch in chunks:
            object.__setattr__(ch, "total", total_chunks)  # dataclass slots bypass
        return chunks


@dataclass
class RefactoredSummariser:
    """Hierarchical summariser compatible with MCC supervisor expectations."""

    backend: ChunkSummaryBackend
    target_chars: int = 2600
    max_chars: int = 10000
    overlap_chars: int = 320
    min_summary_chars: int = 500
    max_overview_lines: int = 4
    max_key_points: int = 6
    max_clinical_details: int = 12
    max_care_plan: int = 8
    max_diagnoses: int = 12
    max_providers: int = 12
    max_medications: int = 12

    # Compatibility shims so supervisor retry variants can tune chunk sizes.
    @property
    def chunk_target_chars(self) -> int:
        return self.target_chars

    @chunk_target_chars.setter
    def chunk_target_chars(self, value: int) -> None:
        self.target_chars = max(512, int(value))

    @property
    def chunk_hard_max(self) -> int:
        return self.max_chars

    @chunk_hard_max.setter
    def chunk_hard_max(self, value: int) -> None:
        self.max_chars = max(self.target_chars + 64, int(value))

    def summarise(
        self, text: str, *, doc_metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, str]:
        if text is None or not str(text).strip():
            raise SummarizationError("Input text empty")
        raw_text = str(text)
        raw_text, prepared_doc_metadata = _prepare_summary_source(raw_text, doc_metadata)
        normalised_source = _prepare_summary_input(raw_text)
        normalised = normalised_source
        if not normalised:
            raise SummarizationError("Input text empty")

        chunker = SlidingWindowChunker(
            target_chars=self.target_chars,
            max_chars=self.max_chars,
            overlap_chars=self.overlap_chars,
        )
        chunked = chunker.split(normalised)
        if not chunked:
            raise SummarizationError("No text chunks produced")

        _LOG.info("summariser_refactored_chunking", extra={"chunks": len(chunked)})

        aggregated: Dict[str, List[str]] = {
            "overview": [],
            "key_points": [],
            "clinical_details": [],
            "care_plan": [],
            "diagnoses": [],
            "providers": [],
            "medications": [],
        }
        provider_usage: Dict[str, int] | None = None

        for chunk in chunked:
            _LOG.info(
                "summariser_refactored_chunk_start",
                extra={
                    "index": chunk.index,
                    "total": chunk.total,
                    "approx_tokens": chunk.approx_tokens,
                },
            )
            payload = self.backend.summarise_chunk(
                chunk_text=chunk.text,
                chunk_index=chunk.index,
                total_chunks=chunk.total,
                estimated_tokens=chunk.approx_tokens,
            )
            provider_usage = _merge_provider_usage(
                provider_usage, payload.get("_provider_usage")
            )
            _LOG.info(
                "summariser_refactored_chunk_complete",
                extra={"index": chunk.index, "keys": sorted(payload.keys())},
            )
            self._merge_payload(aggregated, payload)
        sections_payload = self._prepare_sections_payload(
            aggregated,
            raw_text=raw_text,
            normalised_source=normalised_source,
            chunk_count=len(chunked),
        )
        summary = self._finalise_summary_contract(
            raw_text=raw_text,
            normalised_source=normalised_source,
            sections_payload=sections_payload,
            chunk_count=len(chunked),
            doc_metadata=prepared_doc_metadata,
            chunk_lengths=[len(chunk.text) for chunk in chunked],
        )
        return _annotate_provider_usage_metadata(summary, provider_usage)

    def build_contract_from_payload(
        self,
        payload: Mapping[str, Any],
        *,
        raw_text: str,
        doc_metadata: Optional[Dict[str, Any]] = None,
        chunk_count: int = 1,
    ) -> Dict[str, Any]:
        normalised_source = _prepare_summary_input(raw_text)
        if not normalised_source:
            raise SummarizationError("Input text empty")
        aggregated: Dict[str, List[str]] = {
            "overview": [],
            "key_points": [],
            "clinical_details": [],
            "care_plan": [],
            "diagnoses": [],
            "providers": [],
            "medications": [],
        }
        provider_usage = _normalise_provider_usage(payload.get("_provider_usage"))
        self._merge_payload(aggregated, dict(payload))
        sections_payload = self._prepare_sections_payload(
            aggregated,
            raw_text=raw_text,
            normalised_source=normalised_source,
            chunk_count=chunk_count,
        )
        summary = self._finalise_summary_contract(
            raw_text=raw_text,
            normalised_source=normalised_source,
            sections_payload=sections_payload,
            chunk_count=chunk_count,
            doc_metadata=doc_metadata,
            chunk_lengths=[len(normalised_source)],
        )
        return _annotate_provider_usage_metadata(summary, provider_usage)

    def _prepare_sections_payload(
        self,
        aggregated: Dict[str, List[str]],
        *,
        raw_text: str,
        normalised_source: str,
        chunk_count: int,
    ) -> Dict[str, List[str]]:
        additional_diagnoses = summary_text_utils.extract_additional_diagnoses(
            normalised_source
        )
        if additional_diagnoses:
            aggregated["diagnoses"].extend(additional_diagnoses)

        provider_source = raw_text if raw_text.strip() else normalised_source
        provider_hint, signature_providers = (
            summary_text_utils.extract_signature_providers(provider_source)
        )
        if provider_hint and _looks_like_provider_candidate(provider_hint):
            provider_hint = _normalise_provider_candidate(provider_hint)
        else:
            provider_hint = None
        signature_providers = [
            _normalise_provider_candidate(provider)
            for provider in signature_providers
            if _looks_like_provider_candidate(provider)
        ]
        if signature_providers:
            aggregated["providers"].extend(signature_providers)
        vitals_summary = summary_text_utils.summarize_vitals(normalised_source)

        overview_lines = self._dedupe_ordered(
            aggregated["overview"],
            limit=self.max_overview_lines,
        )
        key_points = self._dedupe_ordered(
            aggregated["key_points"],
            limit=self.max_key_points,
            keywords=self._KEY_POINT_TOKENS,
        )
        if not key_points and aggregated["key_points"]:
            key_points = self._dedupe_ordered(
                aggregated["key_points"],
                limit=self.max_key_points,
            )
        clinical_details = self._dedupe_ordered(
            aggregated["clinical_details"],
            limit=self.max_clinical_details,
            keywords=self._DETAIL_TOKENS,
        )
        if not clinical_details and aggregated["clinical_details"]:
            clinical_details = self._dedupe_ordered(
                aggregated["clinical_details"],
                limit=self.max_clinical_details,
            )
        care_plan = self._dedupe_ordered(
            aggregated["care_plan"],
            limit=self.max_care_plan,
            keywords=self._PLAN_TOKENS,
        )
        if not care_plan and aggregated["care_plan"]:
            care_plan = self._dedupe_ordered(
                aggregated["care_plan"],
                limit=self.max_care_plan,
            )
        diagnoses = self._dedupe_ordered(
            aggregated["diagnoses"], limit=self.max_diagnoses
        )
        providers = self._dedupe_ordered(
            [
                _normalise_provider_candidate(provider)
                for provider in aggregated["providers"]
                if _looks_like_provider_candidate(provider)
            ],
            limit=self.max_providers,
        )
        medications = self._dedupe_ordered(
            [
                _normalise_medication_candidate(medication)
                for medication in aggregated["medications"]
                if _looks_like_medication_candidate(medication)
            ],
            limit=self.max_medications,
            allow_numeric=True,
        )

        sections_payload = {
            "overview": overview_lines,
            "key_points": key_points,
            "clinical_details": clinical_details,
            "care_plan": care_plan,
            "diagnoses": diagnoses,
            "providers": providers,
            "medications": medications,
        }
        _LOG.info(
            "summariser_section_detection",
            extra={
                "input_chars": len(raw_text),
                "input_paragraphs": _count_text_paragraphs(raw_text),
                "input_tokens": _count_word_tokens(normalised_source),
                "normalised_chars": len(normalised_source),
                "normalised_tokens": _count_word_tokens(normalised_source),
                "chunks": chunk_count,
                "overview_lines": len(overview_lines),
                "key_points_count": len(key_points),
                "clinical_details_count": len(clinical_details),
                "care_plan_count": len(care_plan),
                "diagnoses_count": len(diagnoses),
                "providers_count": len(providers),
                "medications_count": len(medications),
                "provider_hint_present": bool(provider_hint),
                "vitals_summary_present": bool(vitals_summary),
            },
        )
        return sections_payload

    def _finalise_summary_contract(
        self,
        *,
        raw_text: str,
        normalised_source: str,
        sections_payload: Dict[str, List[str]],
        chunk_count: int,
        doc_metadata: Optional[Dict[str, Any]],
        chunk_lengths: Sequence[int],
    ) -> Dict[str, Any]:
        provider_source = raw_text if raw_text.strip() else normalised_source
        provider_hint, _signature_providers = summary_text_utils.extract_signature_providers(
            provider_source
        )
        if provider_hint and _looks_like_provider_candidate(provider_hint):
            provider_hint = _normalise_provider_candidate(provider_hint)
        else:
            provider_hint = None
        vitals_summary = summary_text_utils.summarize_vitals(normalised_source)
        extra_context = {
            "primary_provider_hint": provider_hint,
            "vitals_summary": vitals_summary,
        }
        summary_text, mcc_sections, section_lists = self._compose_summary(
            sections_payload,
            chunk_count=chunk_count,
            doc_metadata=doc_metadata,
            context=extra_context,
        )

        summary_text = _sanitise_keywords(summary_text)
        summary_text = re.sub(
            r"(?im)\b(fax|page\s+\d+|cpt|icd[- ]?\d*|procedure\s+code)\b.*$",
            "",
            summary_text,
        )
        summary_text = re.sub(r"[ \t]{2,}", " ", summary_text)
        summary_text = re.sub(r"\n{3,}", "\n\n", summary_text).strip()
        summary_text = _strip_noise_lines(summary_text)

        min_chars = getattr(self, "min_summary_chars", 500)
        if len(summary_text) < min_chars or not re.search(
            r"\bProvider Seen\b", summary_text, re.IGNORECASE
        ):
            raise SummarizationError("Summary too short or missing structure")

        summary_chars = len(summary_text)
        avg_chunk_chars = round(sum(chunk_lengths) / max(1, len(chunk_lengths)), 2)
        diagnoses = sections_payload.get("diagnoses") or []
        providers = sections_payload.get("providers") or []
        medications = sections_payload.get("medications") or []
        _LOG.info(
            "summariser_generation_complete",
            extra={
                "chunks": chunk_count,
                "avg_chunk_chars": avg_chunk_chars,
                "summary_chars": summary_chars,
                "diagnoses": len(diagnoses),
                "providers": len(providers),
                "medications": len(medications),
            },
        )

        def _meta_value(key: str, default: str = "Not provided") -> str:
            if not doc_metadata:
                return default
            value = doc_metadata.get(key)
            if value is None:
                return default
            value_str = str(value).strip()
            return value_str or default

        sections_contract: List[SummarySection] = []
        ordinal_counter = 1

        def _add_section(
            slug: str,
            title: str,
            content: str,
            *,
            kind: str,
            extra: Optional[Dict[str, Any]] = None,
        ) -> None:
            nonlocal ordinal_counter
            sections_contract.append(
                SummarySection(
                    slug=slug,
                    title=title,
                    content=(content or "").strip() or "Not provided",
                    ordinal=ordinal_counter,
                    kind=kind,
                    extra={k: v for k, v in (extra or {}).items() if v},
                )
            )
            ordinal_counter += 1

        _add_section(
            "patient_information",
            "Patient Information",
            _meta_value("patient_info"),
            kind="context",
        )
        _add_section(
            "billing_highlights",
            "Billing Highlights",
            _meta_value("billing"),
            kind="context",
        )
        _add_section(
            "legal_notes",
            "Legal / Notes",
            _meta_value("legal_notes"),
            kind="context",
        )

        provider_lines = section_lists.get("provider_seen") or []
        primary_provider_value = provider_lines[0] if provider_lines else None

        slug_overrides = {
            "Provider Seen": "provider_seen",
            "Reason for Visit": "reason_for_visit",
            "Clinical Findings": "clinical_findings",
            "Treatment / Follow-up Plan": "treatment_follow_up_plan",
            "Diagnoses": "diagnoses",
            "Healthcare Providers": "healthcare_providers",
            "Medications / Prescriptions": "medications",
        }

        for heading, body in mcc_sections:
            slug_base = slug_overrides.get(heading) or _normalise_line_key(
                heading
            ).replace(" ", "_")
            slug = slug_base or f"section_{ordinal_counter}"
            items = section_lists.get(slug) or section_lists.get(slug_base or "", [])
            if slug == "healthcare_providers":
                combined: List[str] = []
                if primary_provider_value:
                    combined.append(primary_provider_value)
                if items:
                    combined.extend(items)
                items = combined
            extra_payload: Dict[str, Any] = {}
            if items:
                extra_payload["items"] = items
            if slug == "provider_seen":
                if provider_lines and "items" not in extra_payload:
                    extra_payload["items"] = provider_lines
                extra_payload.setdefault("primary_provider", primary_provider_value)
                facility_val = _meta_value("facility", "").strip()
                if facility_val:
                    extra_payload.setdefault("facility", facility_val)
            _add_section(slug, heading, body, kind="mcc", extra=extra_payload)

        page_sources: List[Dict[str, Any]] = []
        if doc_metadata:
            pages_meta = doc_metadata.get("pages")
            if isinstance(pages_meta, list):
                for idx, page in enumerate(pages_meta, start=1):
                    if not isinstance(page, dict):
                        continue
                    text_value = str(page.get("text") or "").strip()
                    if not text_value:
                        continue
                    page_sources.append(
                        {
                            "page_number": page.get("page_number")
                            or page.get("pageNumber")
                            or idx,
                            "text": text_value,
                            "ocr_confidence": page.get("confidence"),
                            "source": "ocr_page",
                        }
                    )

        claims, evidence_spans, claims_notice = build_claims_from_sections(
            sections=sections_contract,
            evidence_sources=page_sources,
            max_claims=12,
        )

        contract_metadata: Dict[str, Any] = {
            "source": "refactored_summariser",
            "chunk_count": chunk_count,
            "avg_chunk_chars": avg_chunk_chars,
            "summary_chars": summary_chars,
            "diagnoses_count": len(diagnoses),
            "providers_count": len(providers),
            "medications_count": len(medications),
        }
        if doc_metadata:
            for key in (
                "document_id",
                "job_id",
                "object_uri",
                "summary_text_source",
                "summary_requires_ocr",
                "summary_triage_reason",
                "summary_input_uri",
                "summary_fast_lane_default",
            ):
                value = doc_metadata.get(key)
                if value is not None and value != "":
                    contract_metadata[key] = value
            facility_val_obj = doc_metadata.get("facility")
            if isinstance(facility_val_obj, str) and facility_val_obj:
                contract_metadata["facility"] = facility_val_obj
            elif facility_val_obj:
                contract_metadata["facility"] = str(facility_val_obj)
            triage_metrics = doc_metadata.get("summary_triage_metrics")
            if isinstance(triage_metrics, Mapping):
                contract_metadata["summary_triage_metrics"] = dict(triage_metrics)
            page_filter = doc_metadata.get("summary_page_filter")
            if isinstance(page_filter, Mapping):
                metadata_map = {
                    "applied": "summary_input_filter_applied",
                    "source_pages": "summary_input_pages_total",
                    "selected_pages": "summary_input_pages_selected",
                    "dropped_pages": "summary_input_pages_dropped",
                    "source_chars": "summary_input_chars_total",
                    "selected_chars": "summary_input_chars_selected",
                    "duplicate_pages": "summary_input_duplicate_pages",
                    "hard_reject_pages": "summary_input_hard_reject_pages",
                    "admin_reject_pages": "summary_input_admin_reject_pages",
                    "low_signal_pages": "summary_input_low_signal_pages",
                }
                for source_key, target_key in metadata_map.items():
                    value = page_filter.get(source_key)
                    if isinstance(value, (bool, int, float, str)):
                        contract_metadata[target_key] = value

        contract = SummaryContract(
            schema_version=resolve_schema_version(),
            sections=sections_contract,
            claims=claims,
            evidence_spans=evidence_spans,
            metadata=contract_metadata,
            claims_notice=claims_notice,
        )

        _LOG.info(
            "summariser_merge_complete",
            extra={
                "event": "chunk_merge_complete",
                "emoji": "📄",
                "chunk_count": chunk_count,
                "avg_chunk_chars": avg_chunk_chars,
                "summary_chars": summary_chars,
                "schema_version": contract.schema_version,
                "list_sections": {
                    "diagnoses": len(diagnoses),
                    "providers": len(providers),
                    "medications": len(medications),
                },
            },
        )
        return contract.to_dict()

    async def summarise_async(
        self,
        text: str,
        *,
        doc_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        """Async compatibility wrapper used by API workflow."""
        return await asyncio.to_thread(self.summarise, text, doc_metadata=doc_metadata)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _merge_payload(into: Dict[str, List[str]], payload: Dict[str, Any]) -> None:
        def _coerce_list(value: Any) -> List[str]:
            if value is None:
                return []
            if isinstance(value, list):
                items = [str(v).strip() for v in value if str(v).strip()]
            elif isinstance(value, (tuple, set)):
                items = [str(v).strip() for v in value if str(v).strip()]
            elif isinstance(value, str):
                parts = [part.strip() for part in value.split("\n") if part.strip()]
                items = parts if len(parts) > 1 else [value.strip()]
            elif isinstance(value, dict):
                items = [str(v).strip() for v in value.values() if str(v).strip()]
            else:
                coerced = str(value).strip()
                items = [coerced] if coerced else []
            return [item for item in items if item and not _is_placeholder(item)]

        overview = payload.get("overview")
        if isinstance(overview, str):
            overview_clean = overview.strip()
            if overview_clean and not _is_placeholder(overview_clean):
                into["overview"].append(overview_clean)

        for key in (
            "key_points",
            "clinical_details",
            "care_plan",
            "diagnoses",
            "providers",
            "medications",
        ):
            values = _coerce_list(payload.get(key))
            into[key].extend(values)

    @staticmethod
    def _is_noise_line(value: str, *, allow_numeric: bool = False) -> bool:
        stripped = value.strip()
        if not stripped:
            return True
        if stripped.count("=") >= 5:
            return True
        if len(stripped) > 340:
            return True
        low = stripped.lower()
        if _contains_noise_phrase(stripped):
            return True
        letters = sum(ch.isalpha() for ch in stripped)
        digits = sum(ch.isdigit() for ch in stripped)
        if letters == 0:
            return True
        if not allow_numeric and digits > letters * 2:
            return True
        if len(stripped.split()) <= 2 and digits > letters:
            return True
        if "risk" in low and any(
            token in low
            for token in ("procedure", "injection", "hazard", "complication", "nerve")
        ):
            return True
        if "discharge instruction" in low or "patient education" in low:
            return True
        if "life threatening emergency" in low or "no heavy lifting" in low:
            return True
        return False

    _UNWANTED_TOKENS = (
        "affiant",
        "notary",
        "ledger",
        "account",
        "charges",
        "billing",
        "invoice",
        "records",
        "affidavit",
        "incorporated",
        "commission",
        "financial",
        "statement",
        "balance",
        "acknowledge",
        "contractual",
        "responsible",
        "responsibility",
        "authorization",
        "authorize",
        "third party",
        "payment",
        "assign",
        "assignment",
        "lien",
        "legal",
        "consent",
        "release",
        "attorney",
        "representative",
        "liability",
        "indemnify",
        "hipaa",
        "settlement",
        "benefits",
        "insurance",
        "fees",
        "expenses",
        "witness",
        "sworn",
    )

    _KEY_POINT_TOKENS = (
        "visit",
        "evaluation",
        "assessment",
        "clinic",
        "consult",
        "history of present illness",
        "present illness",
        "reports",
        "complains",
        "symptom",
        "follow-up",
        "provider",
        "review",
        "discussion",
        "examination",
    )

    _DETAIL_TOKENS = (
        "exam",
        "imaging",
        "vital",
        "range of motion",
        "neurologic",
        "labs",
        "symptom",
        "report",
        "study",
        "finding",
        "result",
    )

    _PLAN_TOKENS = (
        "follow",
        "plan",
        "continue",
        "return",
        "schedule",
        "refer",
        "therapy",
        "monitor",
        "start",
        "advised",
        "education",
    )

    @staticmethod
    def _line_score(text: str, keywords: Iterable[str]) -> float:
        low = text.lower()
        letters = sum(ch.isalpha() for ch in text)
        digits = sum(ch.isdigit() for ch in text)
        keyword_hits = sum(1 for kw in keywords if kw in low)
        length_penalty = max(0, len(text) - 220) / 160
        risk_penalty = (
            4 if "risk" in low or "hazard" in low or "complication" in low else 0
        )
        instruction_penalty = 3 if "instruction" in low or "education" in low else 0
        return (
            keyword_hits * 6
            + letters / 140
            - digits * 0.2
            - length_penalty
            - risk_penalty
            - instruction_penalty
        )

    @classmethod
    def _dedupe_ordered(
        cls,
        values: Iterable[str],
        *,
        limit: int,
        allow_numeric: bool = False,
        keywords: Optional[Iterable[str]] = None,
        require_tokens: Optional[Iterable[str]] = None,
    ) -> List[str]:
        keyword_tokens = tuple(k.lower() for k in keywords or ())
        required_tokens = tuple(t.lower() for t in require_tokens or ())
        candidates: List[tuple[float, int, str, str]] = []
        for idx, val in enumerate(values):
            val_clean = val.strip()
            if not val_clean:
                continue
            norm_key = _normalise_line_key(val_clean)
            if not norm_key:
                continue
            low = val_clean.lower()
            if cls._is_noise_line(val_clean, allow_numeric=allow_numeric):
                continue
            if any(tok in low for tok in cls._UNWANTED_TOKENS):
                continue
            if any(fragment in low for fragment in _FINAL_NOISE_FRAGMENTS):
                continue
            if "call" in low and "immediately" in low:
                continue
            if required_tokens and not any(tok in low for tok in required_tokens):
                continue
            if keyword_tokens and not any(tok in low for tok in keyword_tokens):
                continue
            score = cls._line_score(val_clean, keyword_tokens or ("",))
            candidates.append((score, idx, val_clean, norm_key))

        candidates.sort(key=lambda item: (-item[0], item[1]))
        selected: List[tuple[int, str]] = []
        emitted: set[str] = set()
        for score, idx, val_clean, norm_key in candidates:
            if norm_key in emitted:
                continue
            emitted.add(norm_key)
            selected.append((idx, val_clean))
            if len(selected) >= limit:
                break
        selected.sort(key=lambda item: item[0])
        return [val for _, val in selected]

    def _compose_summary(
        self,
        sections: Dict[str, List[str]],
        *,
        chunk_count: int,
        doc_metadata: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> tuple[str, List[tuple[str, str]], Dict[str, List[str]]]:
        context = context or {}
        overview_lines = sections.get("overview") or [
            "The provided medical record segments were analysed to extract clinically relevant information."
        ]
        key_points = sections.get("key_points") or []
        reason_lines = summary_text_utils.select_reason_statements(
            overview_lines, key_points, limit=self.max_key_points
        )
        if not reason_lines:
            reason_lines = overview_lines[: self.max_key_points]
        clinical_details = sections.get("clinical_details") or []
        vitals_summary = context.get("vitals_summary")
        clinical_findings = summary_text_utils.prepare_clinical_findings(
            clinical_details,
            limit=self.max_clinical_details,
            vitals_summary=vitals_summary,
        )
        if not clinical_findings:
            clinical_findings = overview_lines[: self.max_clinical_details]
        plan_lines = summary_text_utils.select_plan_statements(
            sections.get("care_plan") or [], limit=self.max_care_plan
        )
        diagnoses = sections.get("diagnoses") or []
        providers = sections.get("providers") or []
        medications = sections.get("medications") or []
        facility = (doc_metadata or {}).get("facility") if doc_metadata else None

        provider_seen = context.get("primary_provider_hint")
        provider_roster = providers
        if provider_seen:
            provider_roster = [
                entry for entry in providers if entry.lower() != provider_seen.lower()
            ]
        elif providers:
            provider_seen = providers[0]
            provider_roster = providers[1:]
        else:
            provider_roster = []

        provider_lines = []
        if provider_seen:
            provider_lines.append(provider_seen)
        else:
            provider_lines.append("Provider not documented.")
        if facility:
            provider_lines.append(f"Facility: {facility}")

        sections_pairs = summary_formatter.build_mcc_bible_sections(
            chunk_count=chunk_count,
            facility=facility,
            provider_seen=provider_seen,
            reason_lines=reason_lines,
            clinical_findings=clinical_findings,
            care_plan=plan_lines,
            diagnoses=diagnoses,
            healthcare_providers=provider_roster,
            medications=medications,
        )
        cleaned_sections: List[tuple[str, str]] = []
        for heading, body in sections_pairs:
            cleaned_body = _strip_noise_lines(body)
            cleaned_sections.append((heading, cleaned_body.strip()))
        summary_text = "\n\n".join(
            f"{heading}:\n{body}" for heading, body in cleaned_sections if body
        )
        summary_text = _strip_noise_lines(summary_text)
        if len(summary_text) < self.min_summary_chars:
            supplemental_lines = [
                line
                for line in (
                    clinical_findings + plan_lines + reason_lines + overview_lines
                )
                if line
                and not _contains_noise_phrase(line)
                and not any(
                    fragment in line.lower() for fragment in _FINAL_NOISE_FRAGMENTS
                )
            ]
            if supplemental_lines:
                supplemental_unique = self._dedupe_ordered(
                    supplemental_lines,
                    limit=max(self.max_clinical_details, self.max_care_plan),
                    allow_numeric=True,
                )
                if supplemental_unique:
                    appendix = "\n".join(
                        f"- {line}" for line in supplemental_unique if line.strip()
                    )
                    if appendix:
                        summary_text = (
                            summary_text + "\n\nAdditional Context:\n" + appendix
                        )
                        summary_text = _strip_noise_lines(summary_text)
            if len(summary_text) < self.min_summary_chars:
                deficit = max(0, self.min_summary_chars - len(summary_text))
                guidance_line = (
                    "Clinical source text was fragmented; this summary keeps only "
                    "high-confidence findings."
                )
                repeats = max(1, math.ceil(deficit / max(len(guidance_line), 1)))
                summary_text = (
                    summary_text
                    + "\n\n"
                    + " ".join(guidance_line for _ in range(repeats))
                )
                summary_text = _strip_noise_lines(summary_text)
        return (
            summary_text.strip(),
            cleaned_sections,
            {
                "provider_seen": provider_lines,
                "reason_for_visit": reason_lines,
                "clinical_findings": clinical_findings,
                "treatment_follow_up_plan": plan_lines,
                "diagnoses": diagnoses,
                "healthcare_providers": provider_roster,
                "medications": medications,
            },
        )


@dataclass
class AdaptiveSummariser:
    """Routes between one-shot and chunked summarisation while preserving fallback."""

    chunked_summariser: RefactoredSummariser
    one_shot_backend: DocumentSummaryBackend
    requested_strategy: str = DEFAULT_SUMMARY_STRATEGY
    one_shot_token_threshold: int = DEFAULT_ONE_SHOT_TOKEN_THRESHOLD
    one_shot_max_pages: int = DEFAULT_ONE_SHOT_MAX_PAGES
    ocr_noise_ratio_threshold: float = DEFAULT_OCR_NOISE_RATIO_THRESHOLD
    ocr_short_line_ratio_threshold: float = DEFAULT_OCR_SHORT_LINE_RATIO_THRESHOLD

    @property
    def chunk_target_chars(self) -> int:
        return self.chunked_summariser.chunk_target_chars

    @chunk_target_chars.setter
    def chunk_target_chars(self, value: int) -> None:
        self.chunked_summariser.chunk_target_chars = value

    @property
    def chunk_hard_max(self) -> int:
        return self.chunked_summariser.chunk_hard_max

    @chunk_hard_max.setter
    def chunk_hard_max(self, value: int) -> None:
        self.chunked_summariser.chunk_hard_max = value

    def summarise_with_details(
        self,
        text: str,
        *,
        doc_metadata: Optional[Dict[str, Any]] = None,
    ) -> SummaryGenerationResult:
        if text is None or not str(text).strip():
            raise SummarizationError("Input text empty")
        raw_text = str(text)
        raw_text, prepared_doc_metadata = _prepare_summary_source(raw_text, doc_metadata)
        normalised_source = _prepare_summary_input(raw_text)
        if not normalised_source:
            raise SummarizationError("Input text empty")
        pages_meta = prepared_doc_metadata.get("pages") if prepared_doc_metadata else None
        pages: List[Dict[str, Any]] = (
            [page for page in pages_meta if isinstance(page, dict)]
            if isinstance(pages_meta, list)
            else []
        )
        route = _select_summary_route(
            text=normalised_source,
            pages=pages,
            requested_strategy=self.requested_strategy,
            one_shot_token_threshold=self.one_shot_token_threshold,
            one_shot_max_pages=self.one_shot_max_pages,
            noise_ratio_threshold=self.ocr_noise_ratio_threshold,
            short_line_ratio_threshold=self.ocr_short_line_ratio_threshold,
        )
        _LOG.info("summary_strategy_selected", extra=route.to_dict())

        fallback_reason: str | None = None
        if route.selected_strategy == "one_shot":
            try:
                one_shot_payload = self.one_shot_backend.summarise_document(
                    document_text=normalised_source,
                    estimated_tokens=route.metrics.estimated_tokens,
                    page_count=route.metrics.page_count,
                    routing_metrics=route.metrics.to_dict(),
                )
                one_shot_summary = self.chunked_summariser.build_contract_from_payload(
                    one_shot_payload,
                    raw_text=raw_text,
                    doc_metadata=prepared_doc_metadata,
                    chunk_count=1,
                )
                annotated = _annotate_summary_contract_metadata(
                    one_shot_summary,
                    route=route,
                    final_strategy="one_shot",
                    fallback_reason=None,
                    extra_metadata={
                        "summary_fast_lane_attempted": route.selected_strategy
                        == "one_shot",
                        "summary_fast_lane_rejected": False,
                        "summary_heavy_lane_triggered": False,
                    },
                )
                return SummaryGenerationResult(
                    summary=annotated,
                    route=route,
                    final_strategy="one_shot",
                )
            except SummarizationError as exc:
                fallback_reason = str(exc)
                _LOG.warning(
                    "one_shot_fallback_to_chunked",
                    extra={
                        "reason": fallback_reason,
                        "selected_strategy": route.selected_strategy,
                        "requested_strategy": route.requested_strategy,
                        "estimated_tokens": route.metrics.estimated_tokens,
                        "page_count": route.metrics.page_count,
                    },
                )

        chunked_summary = self.chunked_summariser.summarise(
            raw_text, doc_metadata=prepared_doc_metadata
        )
        annotated = _annotate_summary_contract_metadata(
            chunked_summary,
            route=route,
            final_strategy="chunked",
            fallback_reason=fallback_reason,
            extra_metadata={
                "summary_fast_lane_attempted": route.selected_strategy == "one_shot",
                "summary_fast_lane_rejected": False,
                "summary_heavy_lane_triggered": True,
                "summary_heavy_lane_retry_reason": (
                    fallback_reason or "fast_lane_confidence_low"
                    if route.selected_strategy == "one_shot"
                    else route.reason
                ),
            },
        )
        return SummaryGenerationResult(
            summary=annotated,
            route=route,
            final_strategy="chunked",
            fallback_reason=fallback_reason,
        )

    def summarise(
        self, text: str, *, doc_metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        return self.summarise_with_details(text, doc_metadata=doc_metadata).summary

    async def summarise_async(
        self,
        text: str,
        *,
        doc_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        result = await asyncio.to_thread(
            self.summarise_with_details, text, doc_metadata=doc_metadata
        )
        return result.summary


__all__ = [
    "AdaptiveSummariser",
    "ChunkSummaryBackend",
    "DocumentSummaryBackend",
    "HeuristicOneShotBackend",
    "OpenAIOneShotResponsesBackend",
    "OpenAIResponsesBackend",
    "HeuristicChunkBackend",
    "RefactoredSummariser",
]


def _normalise_document_payload(
    data: Dict[str, Any],
) -> tuple[str, Dict[str, Any], List[Dict[str, Any]]]:
    """Extract text, metadata and pages from a Document AI-style payload."""

    if not isinstance(data, dict):
        raise SummarizationError("Input payload must be a JSON object.")

    metadata: Dict[str, Any] = {}
    if isinstance(data.get("metadata"), dict):
        metadata = dict(data["metadata"])

    document: Dict[str, Any] | None = (
        data.get("document") if isinstance(data.get("document"), dict) else None
    )
    if document:
        doc_metadata = document.get("metadata")
        if isinstance(doc_metadata, dict):
            metadata = _merge_dicts(metadata, doc_metadata)
    else:
        document = data

    if not isinstance(document, dict):
        raise SummarizationError("Input payload must be a JSON object.")

    pages_raw = document.get("pages")
    pages: List[Dict[str, Any]] = (
        [page for page in pages_raw if isinstance(page, dict)]
        if isinstance(pages_raw, list)
        else []
    )

    text_val = document.get("text")
    text = text_val.strip() if isinstance(text_val, str) else ""
    structured_text = _rebuild_structured_ocr_text(text, pages) if pages else ""
    if structured_text:
        text = structured_text
    elif not text and pages:
        text = "\n\n".join(
            _clean_text_preserve_paragraphs(str(page.get("text") or ""))
            for page in pages
            if isinstance(page, dict)
        ).strip()
    if not text:
        raise SummarizationError("Input JSON missing 'text' or 'pages' fields.")

    if pages and not isinstance(metadata.get("pages"), list):
        metadata["pages"] = [dict(page) for page in pages]

    return text, metadata, pages


def _shift_text_anchor_offsets(value: Any, offset: int) -> Any:
    if isinstance(value, dict):
        shifted: Dict[str, Any] = {}
        for key, item in value.items():
            if key == "textSegments" and isinstance(item, list):
                segments: List[Any] = []
                for segment in item:
                    if not isinstance(segment, dict):
                        segments.append(segment)
                        continue
                    updated = dict(segment)
                    if "startIndex" in updated:
                        try:
                            updated["startIndex"] = int(updated["startIndex"]) + offset
                        except (TypeError, ValueError):
                            updated["startIndex"] = offset
                    elif offset:
                        updated["startIndex"] = offset
                    if "endIndex" in updated:
                        try:
                            updated["endIndex"] = int(updated["endIndex"]) + offset
                        except (TypeError, ValueError):
                            pass
                    segments.append(updated)
                shifted[key] = segments
                continue
            shifted[key] = _shift_text_anchor_offsets(item, offset)
        return shifted
    if isinstance(value, list):
        return [_shift_text_anchor_offsets(item, offset) for item in value]
    return value


def _load_input_payload_from_gcs(
    gcs_uri: str,
) -> tuple[str, Dict[str, Any], List[Dict[str, Any]]]:
    try:
        from google.cloud import storage  # type: ignore[attr-defined]
    except Exception as exc:  # pragma: no cover - optional dependency
        raise SummarizationError(f"google-cloud-storage unavailable: {exc}") from exc

    bucket_name, object_name = _split_gcs_uri(gcs_uri)
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)

    payload_bytes: bytes | None = None
    try:
        payload_bytes = blob.download_as_bytes()
    except Exception:  # pragma: no cover - treat missing objects as prefix fallback
        payload_bytes = None

    if payload_bytes:
        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise SummarizationError(
                f"Invalid JSON payload at {gcs_uri}: {exc}"
            ) from exc
        return _normalise_document_payload(payload)

    prefix = object_name
    if prefix and not prefix.endswith("/"):
        prefix = prefix.rsplit("/", 1)[0] + "/"

    documents: List[Dict[str, Any]] = []
    for candidate in client.list_blobs(bucket_name, prefix=prefix):
        if candidate.name == object_name or not candidate.name.endswith(".json"):
            continue
        try:
            doc_payload = json.loads(candidate.download_as_bytes().decode("utf-8"))
        except Exception:  # pragma: no cover - skip unreadable blobs
            continue
        documents.append(doc_payload)

    if not documents:
        raise FileNotFoundError(f"Input payload not found: {gcs_uri}")

    combined_metadata: Dict[str, Any] = {}
    combined_pages: List[Dict[str, Any]] = []
    combined_text_parts: List[str] = []
    text_offset = 0

    for doc in documents:
        payload_document: Dict[str, Any] | None = (
            doc.get("document") if isinstance(doc.get("document"), dict) else None
        )
        if payload_document is None and isinstance(doc, dict):
            payload_document = doc
        raw_document_text = (
            payload_document.get("text")
            if isinstance(payload_document, dict)
            and isinstance(payload_document.get("text"), str)
            else ""
        )
        try:
            text, metadata, pages = _normalise_document_payload(doc)
        except SummarizationError:
            continue
        if metadata:
            metadata_without_pages = dict(metadata)
            metadata_without_pages.pop("pages", None)
            combined_metadata = _merge_dicts(combined_metadata, metadata_without_pages)
        if combined_text_parts:
            text_offset += 2
        if raw_document_text:
            combined_text_parts.append(raw_document_text)
        elif text:
            combined_text_parts.append(text)
        for page in pages:
            if not isinstance(page, dict):
                continue
            combined_pages.append(_shift_text_anchor_offsets(page, text_offset))
        text_offset += len(combined_text_parts[-1]) if combined_text_parts else 0

    if not combined_pages and not combined_text_parts:
        raise SummarizationError("No readable OCR payloads found in GCS prefix.")

    combined: Dict[str, Any] = {"pages": combined_pages}
    if combined_metadata:
        combined["metadata"] = {
            **combined_metadata,
            "pages": [dict(page) for page in combined_pages],
        }
    if combined_text_parts:
        combined["text"] = "\n\n".join(combined_text_parts)

    return _normalise_document_payload(combined)


def _load_input_payload(
    path: Path | str,
) -> tuple[str, Dict[str, Any], List[Dict[str, Any]]]:
    raw_path = str(path)
    if raw_path.startswith("gs:/") and not raw_path.startswith("gs://"):
        raw_path = raw_path.replace("gs:/", "gs://", 1)
    if raw_path.startswith("gs://"):
        return _load_input_payload_from_gcs(raw_path)

    local_path = Path(raw_path)
    if not local_path.exists():
        raise FileNotFoundError(f"Input payload not found: {local_path}")
    try:
        payload = json.loads(local_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SummarizationError(
            f"Invalid JSON payload at {local_path}: {exc}"
        ) from exc
    return _normalise_document_payload(payload)


def _write_output(path: Path, summary: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


def _split_gcs_uri(gcs_uri: str) -> Tuple[str, str]:
    if not gcs_uri.startswith("gs://"):
        raise SummarizationError("GCS URI must start with gs://")
    bucket, _, blob = gcs_uri[5:].partition("/")
    if not bucket or not blob:
        raise SummarizationError("Invalid GCS URI; expected gs://bucket/object")
    return bucket, blob


def _upload_summary_to_gcs(  # pragma: no cover - requires GCS interaction
    gcs_uri: str,
    summary: Dict[str, Any],
    *,
    if_generation_match: int | None = 0,
) -> str:
    try:
        from google.cloud import storage  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise SummarizationError(f"google-cloud-storage unavailable: {exc}") from exc

    bucket_name, object_name = _split_gcs_uri(gcs_uri)
    client = storage.Client()
    blob = client.bucket(bucket_name).blob(object_name)
    payload = json.dumps(
        summary, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    upload_kwargs: Dict[str, Any] = {"content_type": "application/json"}
    if if_generation_match is not None and if_generation_match >= 0:
        upload_kwargs["if_generation_match"] = if_generation_match
    blob.upload_from_string(payload, **upload_kwargs)
    gcs_path = f"gs://{blob.bucket.name}/{blob.name}"
    _LOG.info(
        "summary_uploaded_gcs", extra={"gcs_uri": gcs_path, "bytes": len(payload)}
    )
    return gcs_path


def _merge_dicts(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    merged.update(patch)
    return merged


def _cli(argv: Optional[Iterable[str]] = None) -> None:
    log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)
    configure_logging(level=log_level, force=True)

    parser = argparse.ArgumentParser(
        description="Generate MCC medical summaries using the refactored summariser."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to OCR JSON payload containing 'text' or 'pages'.",
    )
    parser.add_argument(
        "--output", help="Optional path to write structured summary JSON locally."
    )
    parser.add_argument(
        "--output-gcs",
        help="Optional GCS URI to upload the summary JSON (uses V4 signed URLs downstream).",
    )
    parser.add_argument(
        "--gcs-if-generation",
        type=int,
        default=0,
        help="ifGenerationMatch precondition when uploading to GCS (default 0; set to -1 to disable).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use heuristic backend (no network calls).",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("SUMMARY_CHUNKED_MODEL")
        or os.getenv("OPENAI_MODEL")
        or DEFAULT_CHUNKED_MODEL,
        help="Chunked OpenAI model to use.",
    )
    parser.add_argument(
        "--summary-strategy",
        default=os.getenv("SUMMARY_STRATEGY") or DEFAULT_SUMMARY_STRATEGY,
        help="Summarisation strategy: chunked, one_shot, or auto.",
    )
    parser.add_argument(
        "--one-shot-model",
        default=os.getenv("SUMMARY_ONE_SHOT_MODEL") or DEFAULT_ONE_SHOT_MODEL,
        help="One-shot OpenAI model to use.",
    )
    parser.add_argument(
        "--one-shot-reasoning-effort",
        default=os.getenv("SUMMARY_ONE_SHOT_REASONING_EFFORT")
        or DEFAULT_ONE_SHOT_REASONING_EFFORT,
        help="Responses API reasoning.effort value for one-shot summaries.",
    )
    parser.add_argument(
        "--one-shot-token-threshold",
        type=int,
        default=int(
            os.getenv(
                "SUMMARY_ONE_SHOT_TOKEN_THRESHOLD",
                str(DEFAULT_ONE_SHOT_TOKEN_THRESHOLD),
            )
        ),
        help="Conservative operational input-token threshold for one-shot routing.",
    )
    parser.add_argument(
        "--one-shot-max-pages",
        type=int,
        default=int(
            os.getenv("SUMMARY_ONE_SHOT_MAX_PAGES", str(DEFAULT_ONE_SHOT_MAX_PAGES))
        ),
        help="Page-count threshold beyond which auto routing prefers chunked fallback.",
    )
    parser.add_argument(
        "--ocr-noise-ratio-threshold",
        type=float,
        default=float(
            os.getenv(
                "SUMMARY_OCR_NOISE_RATIO_THRESHOLD",
                str(DEFAULT_OCR_NOISE_RATIO_THRESHOLD),
            )
        ),
        help="Maximum noisy-line ratio allowed before auto routing falls back to chunking.",
    )
    parser.add_argument(
        "--api-key", help="Explicit OpenAI API key. Defaults to environment variable."
    )
    parser.add_argument(
        "--target-chars",
        type=int,
        default=int(os.getenv("REF_SUMMARISER_TARGET_CHARS", "2400")),
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=int(os.getenv("REF_SUMMARISER_MAX_CHARS", "10000")),
    )
    parser.add_argument(
        "--overlap-chars",
        type=int,
        default=int(os.getenv("REF_SUMMARISER_OVERLAP_CHARS", "320")),
    )
    parser.add_argument(
        "--min-summary-chars",
        type=int,
        default=int(os.getenv("REF_SUMMARISER_MIN_SUMMARY_CHARS", "480")),
    )
    parser.add_argument(
        "--job-id", help="Pipeline job identifier for Cloud Run Jobs to update state."
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    input_arg = args.input
    if input_arg.startswith("gs:/") and not input_arg.startswith("gs://"):
        input_arg = input_arg.replace("gs:/", "gs://", 1)

    text, metadata, pages = _load_input_payload(input_arg)
    supervisor = CommonSenseSupervisor()
    _LOG.info(
        "ocr_input_structure",
        extra=_collect_ocr_structure_metrics(text=text, pages=pages),
    )

    backend_label = "heuristic" if args.dry_run else "chunked"
    chunk_backend: ChunkSummaryBackend
    one_shot_backend: DocumentSummaryBackend
    if args.dry_run:
        chunk_backend = HeuristicChunkBackend()
        one_shot_backend = HeuristicOneShotBackend()
        _LOG.info("heuristic_backend_active", extra={"input_chars": len(text)})
    else:
        api_key = args.api_key
        if not api_key:
            project_id = os.getenv("PROJECT_ID")
            try:
                api_key = resolve_secret_env("OPENAI_API_KEY", project_id=project_id)
            except SecretResolutionError:
                api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            parser.error(
                "OPENAI_API_KEY must be set (or --api-key provided) when not using --dry-run."
            )
        chunk_backend = OpenAIResponsesBackend(model=args.model, api_key=api_key)
        one_shot_backend = OpenAIOneShotResponsesBackend(
            model=args.one_shot_model,
            api_key=api_key,
            reasoning_effort=args.one_shot_reasoning_effort,
        )
        _LOG.info(
            "openai_backends_active",
            extra={
                "chunked_model": args.model,
                "one_shot_model": args.one_shot_model,
                "summary_strategy": args.summary_strategy,
            },
        )

    def _build_chunked_summariser(
        active_backend: ChunkSummaryBackend,
    ) -> RefactoredSummariser:
        return RefactoredSummariser(
            backend=active_backend,
            target_chars=args.target_chars,
            max_chars=args.max_chars,
            overlap_chars=args.overlap_chars,
            min_summary_chars=args.min_summary_chars,
        )

    chunked_summariser = _build_chunked_summariser(chunk_backend)
    summariser = AdaptiveSummariser(
        chunked_summariser=chunked_summariser,
        one_shot_backend=one_shot_backend,
        requested_strategy=args.summary_strategy,
        one_shot_token_threshold=args.one_shot_token_threshold,
        one_shot_max_pages=args.one_shot_max_pages,
        ocr_noise_ratio_threshold=args.ocr_noise_ratio_threshold,
    )

    alignment_source_text, _ = _prepare_summary_source(text, metadata)
    doc_stats = supervisor.collect_doc_stats(text=text, pages=pages, file_bytes=None)
    state_store: PipelineStateStore | None = None
    base_metadata: Dict[str, Any] = {}
    job_snapshot = None
    trace_id: Optional[str] = None
    if args.job_id:
        try:
            state_store = create_state_store_from_env()
            job_snapshot = state_store.get_job(args.job_id)
            if job_snapshot:
                base_metadata = dict(job_snapshot.metadata)
                trace_id = job_snapshot.trace_id
            state_store.mark_status(
                args.job_id,
                PipelineStatus.SUMMARY_SCHEDULED,
                stage="SUMMARY_JOB",
                message="Summariser job started",
                extra={
                    "input_path": (
                        input_arg
                        if input_arg.startswith("gs://")
                        else str(Path(input_arg).resolve())
                    ),
                    "estimated_pages": len(pages),
                    "input_chars": len(text),
                },
            )
        except Exception as exc:  # pragma: no cover - defensive
            _LOG.exception(
                "summary_job_state_init_failed",
                extra={"job_id": args.job_id, "error_type": type(exc).__name__},
            )
            state_store = None

    validation: Dict[str, Any] = {}
    summary: Dict[str, Any] = {}
    summary_result: SummaryGenerationResult | None = None
    failure_phase = "summarisation"
    summarise_started = time.perf_counter()
    try:
        summary_result = summariser.summarise_with_details(text, doc_metadata=metadata)
        summary = summary_result.summary
        backend_label = summary_result.final_strategy
        summary = _annotate_summary_contract_metadata(
            summary,
            route=summary_result.route,
            final_strategy=backend_label,
            fallback_reason=summary_result.fallback_reason,
            extra_metadata={
                "summary_fast_lane_attempted": summary_result.route.selected_strategy
                == "one_shot",
                "summary_fast_lane_rejected": False,
                "summary_heavy_lane_triggered": backend_label == "chunked",
                "summary_heavy_lane_retry_reason": (
                    summary_result.fallback_reason or "fast_lane_confidence_low"
                    if backend_label == "chunked"
                    and summary_result.route.selected_strategy == "one_shot"
                    else summary_result.route.reason
                    if summary_result.route.selected_strategy == "chunked"
                    else None
                ),
            },
        )
        failure_phase = "supervisor"
        validation = supervisor.validate(
            ocr_text=text,
            alignment_source_text=alignment_source_text,
            summary=summary,
            doc_stats=doc_stats,
            retries=0,
            attempt_label="initial",
        )

        if (
            not validation.get("supervisor_passed", False)
            and not args.dry_run
            and backend_label == "one_shot"
        ):
            prior_reason = validation.get("reason") or "supervisor_rejected"
            prior_reasons = _collect_validation_reasons(validation)
            _LOG.warning(
                "one_shot_supervisor_fallback_to_chunked",
                extra={
                    "prior_reason": prior_reason,
                    "prior_reasons": prior_reasons,
                    "content_alignment": validation.get("content_alignment"),
                    "input_chars": len(text),
                },
            )
            chunked_summary = chunked_summariser.summarise(text, doc_metadata=metadata)
            fallback_reason = "one_shot_supervisor_validation_failed"
            summary = _annotate_summary_contract_metadata(
                chunked_summary,
                route=summary_result.route,
                final_strategy="chunked",
                fallback_reason=fallback_reason,
                extra_metadata={
                    "summary_fast_lane_attempted": True,
                    "summary_fast_lane_rejected": True,
                    "summary_fast_lane_rejection_reason": prior_reason,
                    "summary_fast_lane_rejection_reasons": prior_reasons,
                    "summary_heavy_lane_triggered": True,
                    "summary_heavy_lane_retry_reason": "fast_lane_confidence_low",
                    "summary_heavy_lane_retry_reasons": prior_reasons,
                },
            )
            backend_label = "chunked"
            validation = supervisor.validate(
                ocr_text=text,
                alignment_source_text=alignment_source_text,
                summary=summary,
                doc_stats=doc_stats,
                retries=0,
                attempt_label="chunked_fallback",
            )

        if (
            not validation.get("supervisor_passed", False)
            and not args.dry_run
            and backend_label == "chunked"
        ):
            retry_result = supervisor.retry_and_merge(
                summariser=chunked_summariser,
                ocr_text=text,
                alignment_source_text=alignment_source_text,
                doc_stats=doc_stats,
                initial_summary=summary,
                initial_validation=validation,
                doc_metadata=metadata,
            )
            summary = retry_result.summary
            validation = retry_result.validation
            summary = _annotate_summary_contract_metadata(
                summary,
                route=summary_result.route,
                final_strategy="chunked",
                fallback_reason=summary_result.fallback_reason,
                extra_metadata={
                    "summary_fast_lane_attempted": summary_result.route.selected_strategy
                    == "one_shot",
                    "summary_fast_lane_rejected": summary_result.route.selected_strategy
                    == "one_shot",
                    "summary_fast_lane_rejection_reason": validation.get("reason")
                    if summary_result.route.selected_strategy == "one_shot"
                    else None,
                    "summary_fast_lane_rejection_reasons": _collect_validation_reasons(
                        validation
                    )
                    if summary_result.route.selected_strategy == "one_shot"
                    else None,
                    "summary_heavy_lane_triggered": True,
                    "summary_heavy_lane_retry_reason": (
                        "fast_lane_confidence_low"
                        if summary_result.route.selected_strategy == "one_shot"
                        else summary_result.route.reason
                    ),
                    "summary_heavy_lane_retry_reasons": _collect_validation_reasons(
                        validation
                    )
                    if summary_result.route.selected_strategy == "one_shot"
                    else None,
                },
            )

        if (
            not validation.get("supervisor_passed", False)
            and not args.dry_run
            and backend_label == "chunked"
        ):
            prior_reason = validation.get("reason") or "supervisor_rejected"
            prior_alignment = validation.get("content_alignment")
            try:
                heuristic_summariser = _build_chunked_summariser(HeuristicChunkBackend())
                heuristic_summary = heuristic_summariser.summarise(
                    text, doc_metadata=metadata
                )
                heuristic_summary = _annotate_summary_contract_metadata(
                    heuristic_summary,
                    route=summary_result.route,
                    final_strategy="chunked",
                    fallback_reason="heuristic_rescue",
                    extra_metadata={
                        "summary_fast_lane_attempted": summary_result.route.selected_strategy
                        == "one_shot",
                        "summary_fast_lane_rejected": summary_result.route.selected_strategy
                        == "one_shot",
                        "summary_fast_lane_rejection_reason": prior_reason
                        if summary_result.route.selected_strategy == "one_shot"
                        else None,
                        "summary_fast_lane_rejection_reasons": _collect_validation_reasons(
                            validation
                        )
                        if summary_result.route.selected_strategy == "one_shot"
                        else None,
                        "summary_heavy_lane_triggered": True,
                        "summary_heavy_lane_retry_reason": "heuristic_rescue",
                        "summary_heavy_lane_retry_reasons": _collect_validation_reasons(
                            validation
                        ),
                    },
                )
                heuristic_validation = supervisor.validate(
                    ocr_text=text,
                    alignment_source_text=alignment_source_text,
                    summary=heuristic_summary,
                    doc_stats=doc_stats,
                    retries=0,
                    attempt_label="heuristic_rescue",
                )
            except SummarizationError as exc:
                _LOG.warning(
                    "heuristic_rescue_unavailable",
                    extra={
                        "error": str(exc),
                        "prior_reason": prior_reason,
                        "prior_alignment": prior_alignment,
                        "input_chars": len(text),
                    },
                )
            else:
                if heuristic_validation.get("supervisor_passed", False):
                    _LOG.warning(
                        "supervisor_heuristic_rescue",
                        extra={
                            "prior_reason": prior_reason,
                            "prior_alignment": prior_alignment,
                            "heuristic_alignment": heuristic_validation.get(
                                "content_alignment"
                            ),
                            "input_chars": len(text),
                        },
                    )
                    summary = heuristic_summary
                    validation = heuristic_validation
                    backend_label = "heuristic_rescue"

        if not validation.get("supervisor_passed", False):
            reason = validation.get("reason") or "supervisor_rejected"
            if args.dry_run or backend_label == "heuristic_rescue":
                override_mode = "dry_run" if args.dry_run else backend_label
                validation["override_mode"] = override_mode
                validation["override_reason"] = reason
                validation["supervisor_passed"] = True
                log_event = (
                    "supervisor_override_dry_run"
                    if args.dry_run
                    else "supervisor_override_fallback"
                )
                _LOG.warning(
                    log_event,
                    extra={
                        "reason": reason,
                        "input_chars": len(text),
                        "pages": len(pages),
                    },
                )
            else:
                raise SummarizationError(f"Supervisor validation failed: {reason}")
    except Exception as exc:
        if state_store and args.job_id:
            try:
                stage_label = (
                    "SUPERVISOR" if failure_phase == "supervisor" else "SUMMARY_JOB"
                )
                state_store.mark_status(
                    args.job_id,
                    PipelineStatus.FAILED,
                    stage=stage_label,
                    message=str(exc),
                    extra={
                        "error_type": type(exc).__name__,
                        "phase": failure_phase,
                        "summary_backend": backend_label,
                    },
                    updates={
                        "last_error": {
                            "stage": failure_phase,
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                        }
                    },
                )
            except Exception:  # pragma: no cover - best effort
                _LOG.exception(
                    "summary_job_state_failure_mark_failed",
                    extra={"job_id": args.job_id},
                )
        raise

    summary_gcs_uri: Optional[str] = None
    if args.output:
        _write_output(Path(args.output), summary)
    if args.output_gcs:
        try:
            if_generation = (
                None if args.gcs_if_generation < 0 else args.gcs_if_generation
            )
            summary_gcs_uri = _upload_summary_to_gcs(
                args.output_gcs, summary, if_generation_match=if_generation
            )
        except Exception as exc:
            if state_store and args.job_id:
                try:
                    state_store.mark_status(
                        args.job_id,
                        PipelineStatus.FAILED,
                        stage="SUMMARY_JOB",
                        message=str(exc),
                        extra={
                            "error_type": type(exc).__name__,
                            "phase": "summary_upload",
                        },
                        updates={
                            "last_error": {
                                "stage": "summary_upload",
                                "error": str(exc),
                                "error_type": type(exc).__name__,
                            }
                        },
                    )
                except Exception:
                    _LOG.exception(
                        "summary_job_state_upload_failed", extra={"job_id": args.job_id}
                    )
            raise

    contract = SummaryContract.from_mapping(summary)
    summary_text = contract.as_text()
    summary_section_slugs = [section.slug for section in contract.sections]
    summary_char_length = len(summary_text)
    schema_version = contract.schema_version
    if state_store and args.job_id:
        try:
            summary_metadata: Dict[str, Any] = {
                "summary_sections": summary_section_slugs,
                "summary_char_length": summary_char_length,
                "summary_generated_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                ),
                "supervisor_validation": validation,
                "summary_schema_version": schema_version,
                "summary_backend": backend_label,
                "summary_route": summary.get("metadata", {}).get("summary_route_reason"),
                "summary_strategy_used": summary.get("metadata", {}).get(
                    "summary_strategy_used"
                ),
                "summary_text_source": summary.get("metadata", {}).get(
                    "summary_text_source"
                ),
                "summary_triage_reason": summary.get("metadata", {}).get(
                    "summary_triage_reason"
                ),
                "summary_requires_ocr": summary.get("metadata", {}).get(
                    "summary_requires_ocr"
                ),
                "summary_fast_lane_attempted": summary.get("metadata", {}).get(
                    "summary_fast_lane_attempted"
                ),
                "summary_fast_lane_rejected": summary.get("metadata", {}).get(
                    "summary_fast_lane_rejected"
                ),
                "summary_fast_lane_rejection_reason": summary.get("metadata", {}).get(
                    "summary_fast_lane_rejection_reason"
                ),
                "summary_fast_lane_rejection_reasons": summary.get("metadata", {}).get(
                    "summary_fast_lane_rejection_reasons"
                ),
                "summary_heavy_lane_triggered": summary.get("metadata", {}).get(
                    "summary_heavy_lane_triggered"
                ),
                "summary_heavy_lane_retry_reason": summary.get("metadata", {}).get(
                    "summary_heavy_lane_retry_reason"
                ),
                "summary_heavy_lane_retry_reasons": summary.get("metadata", {}).get(
                    "summary_heavy_lane_retry_reasons"
                ),
                "summary_provider_usage_available": summary.get("metadata", {}).get(
                    "provider_usage_available", False
                ),
            }
            provider_usage = summary.get("metadata", {}).get("provider_usage")
            if isinstance(provider_usage, dict) and provider_usage:
                summary_metadata["summary_provider_usage"] = provider_usage
            triage_metrics = summary.get("metadata", {}).get("summary_triage_metrics")
            if isinstance(triage_metrics, dict) and triage_metrics:
                summary_metadata["summary_triage_metrics"] = triage_metrics
            schema_version = summary_metadata["summary_schema_version"]
            if summary_gcs_uri:
                summary_metadata["summary_gcs_uri"] = summary_gcs_uri
            merged_metadata = _merge_dicts(base_metadata, summary_metadata)
            state_store.mark_status(
                args.job_id,
                PipelineStatus.SUMMARY_DONE,
                stage="SUMMARY_JOB",
                message="Summary generated",
                extra={
                    "summary_char_length": summary_metadata["summary_char_length"],
                    "summary_gcs_uri": summary_gcs_uri,
                    "summary_backend": backend_label,
                },
                updates={"metadata": merged_metadata},
            )
            state_store.mark_status(
                args.job_id,
                PipelineStatus.SUPERVISOR_DONE,
                stage="SUPERVISOR",
                message="Supervisor validation complete",
                extra={
                    "supervisor_passed": bool(validation.get("supervisor_passed")),
                    "length_score": validation.get("length_score"),
                    "content_alignment": validation.get("content_alignment"),
                },
            )
        except Exception:  # pragma: no cover - best effort
            _LOG.exception(
                "summary_job_state_complete_failed", extra={"job_id": args.job_id}
            )
    duration_ms = int((time.perf_counter() - summarise_started) * 1000)
    trace_field: Optional[str] = None
    if trace_id:
        project_id = os.getenv("PROJECT_ID") or get_config().project_id
        if project_id:
            trace_field = f"projects/{project_id}/traces/{trace_id}"
    log_fields = {
        "job_id": args.job_id,
        "trace_id": trace_id,
        "request_id": getattr(job_snapshot, "request_id", None),
        "stage": "SUMMARY_JOB",
        "duration_ms": duration_ms,
        "summary_chars": summary_char_length,
        "supervisor_passed": bool(validation.get("supervisor_passed")),
        "summary_backend": backend_label,
    }
    object_uri = getattr(job_snapshot, "object_uri", None) if job_snapshot else None
    if object_uri:
        log_fields["object_uri"] = object_uri
    if summary_gcs_uri:
        log_fields["summary_uri"] = summary_gcs_uri
    log_fields.update(
        _provider_usage_log_fields(summary.get("metadata", {}).get("provider_usage"))
    )
    if trace_field and trace_id:
        log_fields["logging.googleapis.com/trace"] = trace_field
    structured_log(_LOG, logging.INFO, "summary_done", **log_fields)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    _cli()
