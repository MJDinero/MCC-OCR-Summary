"""Summarisation backend primitives (chunking + LLM adapters)."""

from __future__ import annotations

from dataclasses import dataclass
import re
import json
import logging
import math
import textwrap
from typing import Any, Dict, Iterable, List, Optional, Protocol

from src.errors import SummarizationError
from .text_utils import SENTENCE_SPLIT_RE, clean_merge_fragment, clean_text

_LOG = logging.getLogger("summariser.backend")


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


class OpenAIResponsesBackend:  # pragma: no cover - network heavy, validated by integration tests
    """Concrete backend built around the OpenAI Responses API (GPT-4.1/GPT-5 family)."""

    CHUNK_SYSTEM_PROMPT = textwrap.dedent(
        """
        You are a clinical documentation expert following MCC Bible guidance (Nov 2025).
        Extract ONLY clinical content and return STRICT JSON with these keys:
        - provider_seen: array of 1-3 short sentences naming the clinician(s) or service line that treated the patient (no billing/legal text).
        - reason_for_visit: array summarising the chief complaint, presenting problem, or visit purpose.
        - clinical_findings: array of factual sentences covering exams, diagnostics, vitals, and notable negatives.
        - treatment_plan: array describing orders, procedures, medications, and follow-up instructions.
        - diagnoses: array of diagnostic statements (include ICD-10 codes if explicitly provided).
        - healthcare_providers: array of provider names/roles explicitly referenced.
        - medications: array of medications, therapies, or prescriptions ordered or continued.
        - schema_version: string literal matching "2025-11-16".
        Requirements:
        * Stay faithful to the source text. Do not invent information.
        * Strip consent language, intake-form data, refill reminders, insurance/billing text, and noise phrases (e.g. “Document processed in…” or “I understand that…”).
        * Preserve units, dosages, and time references.
        * Expand abbreviations on first use when context allows.
        * Output MUST be valid JSON with the exact property names above. No markdown or commentary outside the JSON payload.
        """
    ).strip()

    CHUNK_SCHEMA_VERSION = "2025-11-16"
    CHUNK_JSON_SCHEMA: Dict[str, Any] = {
        "name": "chunk_summary_v2025_10_01",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "provider_seen": {"type": "array", "items": {"type": "string"}},
                "reason_for_visit": {"type": "array", "items": {"type": "string"}},
                "clinical_findings": {"type": "array", "items": {"type": "string"}},
                "treatment_plan": {"type": "array", "items": {"type": "string"}},
                "diagnoses": {"type": "array", "items": {"type": "string"}},
                "healthcare_providers": {"type": "array", "items": {"type": "string"}},
                "medications": {"type": "array", "items": {"type": "string"}},
                "schema_version": {"type": "string", "enum": ["2025-11-16"]},
            },
            "required": [
                "provider_seen",
                "reason_for_visit",
                "clinical_findings",
                "treatment_plan",
                "diagnoses",
                "healthcare_providers",
                "medications",
                "schema_version",
            ],
        },
    }

    def __init__(self, model: str = "gpt-4.1-mini", api_key: Optional[str] = None) -> None:
        self.model = model
        self.api_key = api_key
        self._fallback_used = False

    def summarise_chunk(
        self,
        *,
        chunk_text: str,
        chunk_index: int,
        total_chunks: int,
        estimated_tokens: int,
    ) -> Dict[str, Any]:  # pragma: no cover - network path exercised in integration tests
        try:
            from openai import OpenAI  # type: ignore
        except Exception as exc:  # pragma: no cover - dependency resolution
            raise SummarizationError(f"OpenAI SDK unavailable: {exc}") from exc

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
                response_format={
                    "type": "json_schema",
                    "json_schema": self.CHUNK_JSON_SCHEMA,
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

        content = self._collect_output_text(response)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:  # pragma: no cover - salvage path
            raise SummarizationError(
                f"Failed to parse chunk JSON (chunk {chunk_index}): {exc}"
            ) from exc
        if parsed.get("schema_version") != self.CHUNK_SCHEMA_VERSION:
            raise SummarizationError(
                f"Chunk schema_version mismatch: expected {self.CHUNK_SCHEMA_VERSION}, got {parsed.get('schema_version')}"
            )
        return parsed

    @staticmethod
    def _collect_output_text(resp: Any) -> str:
        segments: List[str] = []
        output_items = getattr(resp, "output", None)
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
        fallback = getattr(resp, "output_text", "")
        return str(fallback or "")


class HeuristicChunkBackend(ChunkSummaryBackend):
    """Offline backend that derives structured snippets from OCR text."""

    provider_tokens = ("dr", "doctor", "nurse", "provider", "physician", "practitioner")
    care_plan_tokens = (
        "plan",
        "follow",
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
    provider_pattern = re.compile(r"Dr\\.?\\s+[A-Z][a-z]+(?:\\s+[A-Z][a-z]+)*")
    medication_pattern = re.compile(
        r"\\b([A-Z][a-z]+(?:\\s+[A-Z][a-z]+)*\\s+\\d+\\s*(?:mg|mcg|g))"
    )
    named_med_pattern = re.compile(
        r"\\b([A-Z][a-z]+(?:\\s+[A-Z][a-z]+)*(?:\\s+therapy|\\s+diet))"
    )

    def summarise_chunk(
        self,
        *,
        chunk_text: str,
        chunk_index: int,
        total_chunks: int,
        estimated_tokens: int,
    ) -> Dict[str, Any]:
        _ = (chunk_index, total_chunks, estimated_tokens)
        cleaned = clean_text(chunk_text)
        sentences = [s.strip() for s in SENTENCE_SPLIT_RE.split(cleaned) if s.strip()]
        if not sentences and cleaned:
            sentences = [cleaned]

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
        sentences = merged_sentences or sentences

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
            sent.strip().rstrip(".") for sent in sentences[1:] if len(sent.split()) >= 6
        ][:10]
        if not clinical_details:
            clinical_details = [
                s.strip().rstrip(".") for s in sentences[: max(1, len(sentences) // 2)]
            ]

        care_plan = _select(sentences, self.care_plan_tokens, limit=8)
        if not care_plan and sentences:
            care_plan = [sentences[-1].strip().rstrip(".")]

        diagnoses = _select(sentences, self.diagnosis_tokens, limit=6)
        if not diagnoses:
            diag_hits: List[str] = []
            lowered_text = cleaned.lower()
            for token in self.diagnosis_tokens:
                if token in lowered_text and token.title() not in diag_hits:
                    diag_hits.append(token.title())
            diagnoses = diag_hits

        providers = _select(sentences, self.provider_tokens, limit=5)
        for match in self.provider_pattern.findall(chunk_text):
            normalised = clean_merge_fragment(match)
            if normalised and normalised not in providers:
                providers.append(normalised)

        medications = _select(sentences, self.medication_tokens, limit=6)
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
        reason_sentences: List[str] = []
        for sent in sentences[:4]:
            trimmed = sent.strip().rstrip(".")
            if len(trimmed.split()) < 3:
                continue
            reason_sentences.append(trimmed)
            if len(reason_sentences) >= 3:
                break
        if not reason_sentences and cleaned:
            reason_sentences = [cleaned[:240].strip()]

        provider_seen_lines: List[str] = []
        if providers:
            provider_seen_lines.append(f"Primary provider: {providers[0]}")
            if len(providers) > 1:
                supporting = ", ".join(providers[1:3])
                provider_seen_lines.append(f"Supporting team: {supporting}")
        elif sentences:
            provider_seen_lines = [sentences[0][:200].strip()]

        return {
            "provider_seen": _truncate(provider_seen_lines),
            "reason_for_visit": _truncate(reason_sentences),
            "clinical_findings": _truncate(clinical_details),
            "treatment_plan": _truncate(care_plan),
            "diagnoses": _truncate(diagnoses),
            "healthcare_providers": _truncate(providers),
            "medications": _truncate(medications),
            "schema_version": OpenAIResponsesBackend.CHUNK_SCHEMA_VERSION,
        }


@dataclass(slots=True)
class ChunkedText:
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
                    total=0,
                    approx_tokens=approx_tokens,
                )
            )
            chunk_index += 1
            if end >= length:
                break
            start = max(0, end - self.overlap_chars)
        total_chunks = len(chunks)
        for ch in chunks:
            object.__setattr__(ch, "total", total_chunks)
        return chunks


__all__ = [
    "ChunkSummaryBackend",
    "OpenAIResponsesBackend",
    "HeuristicChunkBackend",
    "ChunkedText",
    "SlidingWindowChunker",
]
