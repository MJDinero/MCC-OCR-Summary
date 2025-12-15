"""Canonical MCC summary contract shared by summariser, PDF writer, and validator."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Sequence

DEFAULT_SUMMARY_SCHEMA_VERSION = os.getenv(
    "SUMMARY_SCHEMA_VERSION", "2025-10-01-contract-v1"
)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def resolve_schema_version() -> str:
    """Resolve the canonical schema version from config, falling back safely."""

    try:
        from src.config import get_config

        return get_config().summary_schema_version
    except Exception:  # pragma: no cover - config loading can fail in tests
        return DEFAULT_SUMMARY_SCHEMA_VERSION


@dataclass(slots=True)
class SummarySection:
    slug: str
    title: str
    content: str
    ordinal: int
    kind: str = "narrative"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "slug": self.slug,
            "title": self.title,
            "content": (self.content or "").strip(),
            "ordinal": self.ordinal,
            "kind": self.kind,
        }
        if self.extra:
            payload["extra"] = self.extra
        return payload


@dataclass(slots=True)
class EvidenceSpan:
    span_id: str
    page: int
    text_snippet: str
    confidence: float | None = None
    source: str = "ocr_page"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "span_id": self.span_id,
            "page": int(self.page),
            "text_snippet": self.text_snippet.strip(),
            "source": self.source,
        }
        if self.confidence is not None:
            payload["confidence"] = float(f"{float(self.confidence):.3f}")
        return payload


@dataclass(slots=True)
class SummaryClaim:
    claim_id: str
    section: str
    value: str
    field_type: str
    status: str
    evidence_refs: list[str]
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "claim_id": self.claim_id,
            "section": self.section,
            "value": self.value.strip(),
            "field_type": self.field_type,
            "status": self.status,
            "evidence_refs": list(self.evidence_refs),
        }
        if self.confidence is not None:
            payload["confidence"] = float(f"{float(self.confidence):.3f}")
        return payload


@dataclass(slots=True)
class SummaryContract:
    schema_version: str
    sections: list[SummarySection]
    claims: list[SummaryClaim]
    evidence_spans: list[EvidenceSpan]
    metadata: dict[str, Any] = field(default_factory=dict)
    claims_notice: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "sections": [section.to_dict() for section in self.sections],
            "_claims": [claim.to_dict() for claim in self.claims],
            "_evidence_spans": [span.to_dict() for span in self.evidence_spans],
            "metadata": {
                **self.metadata,
                "generated_at": self.metadata.get("generated_at", _now_iso()),
            },
        }
        if self.claims_notice:
            payload["_claims_notice"] = self.claims_notice
        return payload

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "SummaryContract":
        sections_payload = payload.get("sections") or []
        sections = [
            SummarySection(
                slug=str(section.get("slug") or section.get("title") or f"section-{idx}"),
                title=str(section.get("title") or section.get("slug") or f"Section {idx}"),
                content=str(section.get("content") or ""),
                ordinal=int(section.get("ordinal") or (idx + 1)),
                kind=str(section.get("kind") or "narrative"),
                extra=dict(section.get("extra") or {}),
            )
            for idx, section in enumerate(sections_payload)
        ]
        claims_payload = payload.get("_claims") or []
        claims = [
            SummaryClaim(
                claim_id=str(item.get("claim_id") or f"claim-{idx}"),
                section=str(item.get("section") or "Unknown"),
                value=str(item.get("value") or ""),
                field_type=str(item.get("field_type") or "unknown"),
                status=str(item.get("status") or "supported"),
                evidence_refs=[str(ref) for ref in item.get("evidence_refs", [])],
                confidence=(float(item["confidence"]) if "confidence" in item else None),
            )
            for idx, item in enumerate(claims_payload)
        ]
        evidence_payload = payload.get("_evidence_spans") or []
        evidence_spans = [
            EvidenceSpan(
                span_id=str(item.get("span_id") or f"span-{idx}"),
                page=int(item.get("page") or 0),
                text_snippet=str(item.get("text_snippet") or item.get("text") or ""),
                confidence=(float(item["confidence"]) if "confidence" in item else None),
                source=str(item.get("source") or "ocr_page"),
            )
            for idx, item in enumerate(evidence_payload)
        ]
        claims_notice = payload.get("_claims_notice")
        metadata = dict(payload.get("metadata") or {})
        schema_version = str(payload.get("schema_version") or resolve_schema_version())
        return cls(
            schema_version=schema_version,
            sections=sections,
            claims=claims,
            evidence_spans=evidence_spans,
            metadata=metadata,
            claims_notice=str(claims_notice) if claims_notice else None,
        )

    def as_text(self) -> str:
        return sections_to_text(self.sections)


def sections_to_text(sections: Sequence[SummarySection]) -> str:
    return "\n\n".join(
        f"{section.title}:\n{section.content.strip()}".strip()
        for section in sections
        if section.content and section.title
    ).strip()


_TOKEN_RE = re.compile(r"[A-Za-z0-9']+")

_MCC_HEADINGS: tuple[tuple[str, str], ...] = (
    ("provider_seen", "Provider Seen"),
    ("reason_for_visit", "Reason for Visit"),
    ("clinical_findings", "Clinical Findings"),
    ("treatment_follow_up_plan", "Treatment / Follow-up Plan"),
    ("diagnoses", "Diagnoses"),
    ("healthcare_providers", "Healthcare Providers"),
    ("medications", "Medications / Prescriptions"),
)


def _tokenize(text: str) -> list[str]:
    return [token.lower() for token in _TOKEN_RE.findall(text or "")]


def _best_snippet(statement: str, source_text: str) -> tuple[str, float] | None:
    if not statement or not source_text:
        return None
    statement_clean = statement.strip()
    statement_low = statement_clean.lower()
    source_low = source_text.lower()
    idx = source_low.find(statement_low)
    if idx >= 0:
        start = max(0, idx - 32)
        end = min(len(source_text), idx + len(statement_clean) + 32)
        snippet = source_text[start:end].strip()
        score = min(0.99, len(statement_clean) / max(len(source_text), 1))
        return snippet or source_text.strip(), score
    statement_tokens = _tokenize(statement_clean)
    if not statement_tokens:
        return None
    source_tokens = set(_tokenize(source_text))
    if not source_tokens:
        return None
    overlap = sum(1 for token in statement_tokens if token in source_tokens)
    if not overlap:
        return None
    ratio = overlap / max(len(statement_tokens), 1)
    for token in statement_tokens:
        pos = source_low.find(token)
        if pos >= 0:
            start = max(0, pos - 40)
            end = min(len(source_text), pos + 160)
            return source_text[start:end].strip(), min(0.95, ratio)
    return source_text[:200].strip(), min(0.75, ratio)


def _normalise_sources(
    sources: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    normalised: list[dict[str, Any]] = []
    if not sources:
        return normalised
    for idx, source in enumerate(sources, start=1):
        text_value = source.get("text") or source.get("text_snippet") or ""
        if not text_value:
            continue
        page_value = source.get("page") or source.get("page_number") or source.get(
            "pageIndex"
        )
        try:
            page_number = int(page_value) if page_value is not None else idx
        except (TypeError, ValueError):
            page_number = idx
        normalised.append(
            {
                "page": page_number,
                "text": str(text_value),
                "confidence": source.get("confidence")
                or source.get("ocr_confidence"),
                "source": source.get("source") or "ocr_page",
            }
        )
    return normalised


def build_claims_from_sections(
    *,
    sections: Sequence[SummarySection],
    evidence_sources: Sequence[Mapping[str, Any]] | None,
    max_claims: int = 12,
    max_statements_per_section: int = 3,
) -> tuple[list[SummaryClaim], list[EvidenceSpan], str | None]:
    sources = _normalise_sources(evidence_sources)
    if not sources:
        return [], [], "evidence_unavailable"

    statements: list[tuple[SummarySection, str, int]] = []
    for section in sections:
        if section.kind not in {"mcc", "clinical"}:
            continue
        count = 0
        for raw_line in section.content.splitlines():
            cleaned = raw_line.strip()
            if not cleaned:
                continue
            if cleaned.startswith("-") or cleaned.startswith("•"):
                cleaned = cleaned[1:].strip()
            if not cleaned:
                continue
            statements.append((section, cleaned, count))
            count += 1
            if count >= max_statements_per_section:
                break
        if not count and section.content.strip():
            statements.append((section, section.content.strip(), 0))

    claims: list[SummaryClaim] = []
    evidence_spans: list[EvidenceSpan] = []
    span_seq = 1
    for section, statement, _ in statements:
        if len(claims) >= max_claims:
            break
        best_match: tuple[int, str, float | None] | None = None
        for source_idx, source in enumerate(sources):
            snippet_score = _best_snippet(statement, source["text"])
            if not snippet_score:
                continue
            snippet, score = snippet_score
            if not snippet:
                continue
            if best_match is None or (score or 0) > (best_match[2] or 0):
                best_match = (
                    source_idx,
                    snippet,
                    score if score is not None else source.get("confidence"),
                )
        if best_match is None:
            continue
        source_idx, snippet, match_score = best_match
        source_entry = sources[source_idx]
        span_id = f"{section.slug}-span-{span_seq}"
        claim_id = f"{section.slug}-claim-{span_seq}"
        span_seq += 1
        span_confidence = (
            float(match_score)
            if match_score is not None
            else source_entry.get("confidence")
        )
        evidence_spans.append(
            EvidenceSpan(
                span_id=span_id,
                page=source_entry["page"],
                text_snippet=snippet,
                confidence=span_confidence,
                source=source_entry.get("source", "ocr_page"),
            )
        )
        claims.append(
            SummaryClaim(
                claim_id=claim_id,
                section=section.title,
                value=statement,
                field_type=section.slug,
                status="supported",
                evidence_refs=[span_id],
                confidence=float(match_score) if match_score is not None else None,
            )
        )

    if not claims:
        return [], [], "no_evidence_matches"
    return claims, evidence_spans, None


def ensure_contract_dict(summary: Mapping[str, Any] | SummaryContract) -> Dict[str, Any]:
    if isinstance(summary, SummaryContract):
        return summary.to_dict()
    if isinstance(summary, Mapping):
        if summary.get("sections"):
            return SummaryContract.from_mapping(summary).to_dict()
        if isinstance(summary.get("Medical Summary"), str):
            legacy_contract = build_contract_from_text(
                str(summary.get("Medical Summary")), metadata=summary
            )
            return legacy_contract.to_dict()
        return SummaryContract.from_mapping(summary).to_dict()
    raise TypeError("Summary payload must be a mapping or SummaryContract")


def _sections_from_text(summary_text: str) -> list[SummarySection]:
    sections: list[SummarySection] = []
    if not summary_text:
        return sections
    heading_lookup = {title.lower(): (slug, title) for slug, title in _MCC_HEADINGS}
    current_slug: str | None = None
    current_title: str | None = None
    buffer: list[str] = []
    ordinal = 1
    for raw_line in summary_text.splitlines():
        stripped = raw_line.strip()
        lookup_key = stripped.rstrip(":").lower()
        if stripped.endswith(":") and lookup_key in heading_lookup:
            if current_title is not None:
                sections.append(
                    SummarySection(
                        slug=current_slug or f"section_{ordinal}",
                        title=current_title,
                        content="\n".join(buffer).strip(),
                        ordinal=ordinal,
                        kind="mcc",
                    )
                )
                ordinal += 1
                buffer = []
            slug, title = heading_lookup[lookup_key]
            current_slug = slug
            current_title = title
        else:
            buffer.append(raw_line)
    if current_title is not None:
        sections.append(
            SummarySection(
                slug=current_slug or f"section_{ordinal}",
                title=current_title,
                content="\n".join(buffer).strip(),
                ordinal=ordinal,
                kind="mcc",
            )
        )
    return sections


def build_contract_from_text(
    summary_text: str,
    *,
    metadata: Mapping[str, Any] | None = None,
    evidence_sources: Sequence[Mapping[str, Any]] | None = None,
    schema_version: str | None = None,
) -> SummaryContract:
    sections = _sections_from_text(summary_text)
    if not sections:
        sections = [
            SummarySection(
                slug="medical_summary",
                title="Medical Summary",
                content=summary_text.strip(),
                ordinal=1,
                kind="narrative",
            )
        ]
    claims, evidence_spans, claims_notice = build_claims_from_sections(
        sections=sections,
        evidence_sources=evidence_sources,
    )
    contract = SummaryContract(
        schema_version=schema_version or resolve_schema_version(),
        sections=sections,
        claims=claims,
        evidence_spans=evidence_spans,
        metadata=dict(metadata or {}),
        claims_notice=claims_notice,
    )
    return contract


__all__ = [
    "SummarySection",
    "SummaryClaim",
    "EvidenceSpan",
    "SummaryContract",
    "build_claims_from_sections",
    "ensure_contract_dict",
    "resolve_schema_version",
    "sections_to_text",
    "build_contract_from_text",
]
