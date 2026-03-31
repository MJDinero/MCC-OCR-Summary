"""Common sense supervisor enforcing structural quality of generated summaries."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Dict, Any, Mapping, Sequence, Iterable, Tuple
import logging
import re
from collections import Counter
import os

from src.models.summary_contract import SummaryContract
from src.services.docai_helper import clean_ocr_output


_LOG = logging.getLogger("supervisor")


_WORD_RE = re.compile(r"[A-Za-z0-9']+")
_HEADING_ONLY_RE = re.compile(r"^\s*(?:[-*]\s*)?[A-Za-z][A-Za-z0-9 /&()'-]{0,80}:\s*$")

_HEADER_TOKENS: tuple[str, ...] = (
    "diagnoses:",
    "clinical findings:",
    "treatment",
    "medications",
    "reason for visit:",
    "provider seen:",
)  # pragma: no cover
_ALIGNMENT_PHRASE_LIMIT = 200

_STOPWORDS: frozenset[str] = frozenset(
    "the and or of to a in for on with at by is are was were be this that from as an it patient patients medical summary plan follow diagnosis".split()
)  # pragma: no cover

_INVALID_SUMMARY_KEYWORDS: frozenset[str] = frozenset(
    {"n/a", "no data", "none", "empty", "tbd"}
)
_PLACEHOLDER_RE = re.compile(
    r"^(?:n/?a|no data|none|empty|tbd)[\s\.\-]*$", re.IGNORECASE
)
_SUMMARY_QUALITY_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "not provided",
        "not documented",
        "provider not documented",
        "provider not documented.",
        "not listed",
        "not listed.",
        "no diagnoses documented",
        "no diagnoses documented.",
        "no medications or prescriptions recorded",
        "no medications or prescriptions recorded.",
    }
)
_SUMMARY_ADMIN_PHRASES: tuple[str, ...] = (
    "authorize my physicians",
    "authorization to disclose protected health information",
    "doctor cosign",
    "nurse review",
    "order status",
    "department status",
    "discharge instructions",
    "call 911",
    "emergency room",
    "risk of non-treatment",
    "does not guarantee result or a cure",
    "medical records affidavit",
    "power of attorney",
    "check your injection site every day",
    "keep an active list of medications",
    "final active medications list",
    "medication agreement",
    "please fill your prescriptions",
    "release of information",
)
_SUMMARY_ADMIN_TOKENS: frozenset[str] = frozenset(
    {
        "affidavit",
        "authorization",
        "consent",
        "billing",
        "invoice",
        "ledger",
        "attorney",
        "discharge",
        "instruction",
        "instructions",
        "cosign",
        "review",
        "department",
        "status",
        "pharmacy",
        "hazard",
        "hipaa",
        "records",
    }
)
_REASON_SIGNAL_TOKENS: frozenset[str] = frozenset(
    {
        "visit",
        "follow",
        "followup",
        "follow-up",
        "pain",
        "injury",
        "complaint",
        "symptom",
        "evaluation",
        "presents",
        "presented",
        "chief",
        "history",
    }
)
_CLINICAL_DENSITY_TOKENS: frozenset[str] = frozenset(
    {
        "pain",
        "injury",
        "lumbar",
        "cervical",
        "ankle",
        "neck",
        "exam",
        "assessment",
        "diagnosis",
        "plan",
        "follow",
        "medication",
        "prescription",
        "imaging",
        "mri",
        "xray",
        "fracture",
        "sprain",
        "strain",
    }
)
_PROVIDER_HINT_RE = re.compile(
    r"\b(?:dr\.?\s+[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,3}|"
    r"physician|provider|nurse practitioner|physician assistant|pa-c|np|m\.d\.|d\.o\.)\b",
    re.IGNORECASE,
)
_MEDICATION_HINT_RE = re.compile(
    r"\b(?:[A-Z][a-z]{3,}(?:\s+[A-Z][a-z]{3,})*\s+\d+\s*(?:mg|mcg|g|ml)|"
    r"\d+\s*(?:mg|mcg|g|ml)|tablet|capsule|patch|ointment|injection|"
    r"ibuprofen|acetaminophen|cyclobenzaprine|lisinopril|hydromorphone|labetalol)\b",
    re.IGNORECASE,
)
_DIAGNOSIS_HINT_RE = re.compile(
    r"\b(?:[A-TV-Z][0-9][0-9A-Z](?:\.[0-9A-Z]+)?|"
    r"pain|strain|sprain|fracture|radiculopathy|hypertension|diabetes|"
    r"migraine|injury|syndrome|disease|arthritis|contusion|spasm)\b",
    re.IGNORECASE,
)


def _strip_section_headers(summary_text: str) -> str:
    """Remove heading-only lines while preserving inline clinical labels."""

    if not summary_text:
        return ""
    cleaned_lines: list[str] = []
    for raw_line in summary_text.splitlines():
        line = raw_line.strip()
        if not line:
            cleaned_lines.append("")
            continue
        if _HEADING_ONLY_RE.match(line):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def _normalise_mb(size_bytes: int | float | None) -> float:
    if not size_bytes:
        return 0.0
    return round(float(size_bytes) / (1024 * 1024), 3)


def _extract_summary_text(summary: Dict[str, Any]) -> str:
    if not summary:
        return ""
    if "sections" in summary:
        try:
            contract = SummaryContract.from_mapping(summary)
            return contract.as_text()
        except Exception:  # pragma: no cover - fallback for malformed payloads
            pass
    if isinstance(summary.get("Medical Summary"), str):
        return summary["Medical Summary"]
    parts: list[str] = []
    for value in summary.values():
        if isinstance(value, str):
            parts.append(value)
    return "\n".join(parts)


def _is_section_placeholder(content: str) -> bool:
    lowered = content.strip().lower().rstrip(".")
    if not lowered:
        return True
    if _PLACEHOLDER_RE.fullmatch(lowered):
        return True
    return lowered in _SUMMARY_QUALITY_PLACEHOLDERS


def _section_items(section: Any) -> list[str]:
    extra = getattr(section, "extra", None)
    raw_items = extra.get("items") if isinstance(extra, dict) else None
    if isinstance(raw_items, list):
        items = [str(item).strip(" -") for item in raw_items if str(item).strip()]
        if items:
            return items
    content = str(getattr(section, "content", "") or "")
    items = [
        line.strip(" -")
        for line in content.splitlines()
        if line.strip() and not _HEADING_ONLY_RE.match(line.strip())
    ]
    return [item for item in items if item]


def _admin_phrase_hits(text: str) -> int:
    lowered = text.lower()
    return sum(lowered.count(phrase) for phrase in _SUMMARY_ADMIN_PHRASES)


def _admin_token_hits(tokens: Sequence[str]) -> int:
    return sum(1 for token in tokens if token in _SUMMARY_ADMIN_TOKENS)


def _clinical_density_hits(tokens: Sequence[str]) -> int:
    return sum(1 for token in tokens if token in _CLINICAL_DENSITY_TOKENS)


def _assess_structured_summary_quality(summary: Mapping[str, Any]) -> Dict[str, Any]:
    default = {
        "passed": True,
        "missing_key_sections": [],
        "admin_dominant_sections": [],
        "mixed_sections": [],
        "clinically_dense_sections": 0,
        "admin_token_ratio": 0.0,
        "reasons": [],
    }
    sections_raw = summary.get("sections")
    if not isinstance(sections_raw, list):
        return default

    try:
        contract = SummaryContract.from_mapping(dict(summary))
    except Exception:
        failed = dict(default)
        failed.update({"passed": False, "reasons": ["invalid_summary_contract"]})
        return failed

    key_slugs = (
        "provider_seen",
        "reason_for_visit",
        "clinical_findings",
        "treatment_follow_up_plan",
        "diagnoses",
        "medications",
    )
    section_by_slug = {section.slug: section for section in contract.sections}
    missing_sections: list[str] = []
    admin_dominant_sections: list[str] = []
    mixed_sections: list[str] = []
    clinically_dense_sections = 0
    total_admin_hits = 0
    total_tokens = 0

    for slug in key_slugs:
        section = section_by_slug.get(slug)
        content = (section.content or "").strip() if section else ""
        if not content or _is_section_placeholder(content):
            missing_sections.append(slug)
            continue

        tokens = [match.group(0).lower() for match in _WORD_RE.finditer(content)]
        if not tokens:
            missing_sections.append(slug)
            continue
        admin_hits = _admin_phrase_hits(content) * 3 + _admin_token_hits(tokens)
        clinical_hits = _clinical_density_hits(tokens)
        total_admin_hits += admin_hits
        total_tokens += len(tokens)

        if admin_hits >= 4 and (admin_hits / max(1, len(tokens))) >= 0.12:
            admin_dominant_sections.append(slug)

        if slug == "provider_seen":
            provider_sentence_text = re.sub(
                r"\b(?:M\.D\.|D\.O\.|P\.A-C\.|P\.A\.|N\.P\.)\b",
                lambda match: match.group(0).replace(".", ""),
                content,
                flags=re.IGNORECASE,
            )
            sentence_count = len(re.findall(r"[.!?]", provider_sentence_text))
            if (
                not _PROVIDER_HINT_RE.search(content)
                or _MEDICATION_HINT_RE.search(content)
                or admin_hits >= 2
                or sentence_count > 1
                or len(tokens) > 18
            ):
                mixed_sections.append(slug)
            else:
                clinically_dense_sections += 1
            continue

        if slug == "reason_for_visit":
            reason_hits = sum(1 for token in tokens if token in _REASON_SIGNAL_TOKENS)
            line_count = len([line for line in content.splitlines() if line.strip()])
            if (
                admin_hits >= 3
                or any(
                    phrase in content.lower()
                    for phrase in (
                        "doctor cosign",
                        "nurse review",
                        "please fill your prescriptions",
                        "authorize my physicians",
                        "keep an active list of medications",
                    )
                )
                or reason_hits < 1
                or (line_count > 6 and reason_hits < 3)
            ):
                mixed_sections.append(slug)
            else:
                clinically_dense_sections += 1
            continue

        if slug == "diagnoses":
            items = _section_items(section)
            valid_items = [
                item
                for item in items
                if _DIAGNOSIS_HINT_RE.search(item)
                and _admin_phrase_hits(item) == 0
            ]
            if not valid_items or (len(items) >= 2 and len(valid_items) * 2 < len(items)):
                mixed_sections.append(slug)
            else:
                clinically_dense_sections += 1
            continue

        if slug == "medications":
            items = _section_items(section)
            valid_items = [
                item
                for item in items
                if _MEDICATION_HINT_RE.search(item)
                and _admin_phrase_hits(item) == 0
            ]
            if not valid_items or (len(items) >= 2 and len(valid_items) * 2 < len(items)):
                mixed_sections.append(slug)
            else:
                clinically_dense_sections += 1
            continue

        if clinical_hits >= 2 and admin_hits <= 1:
            clinically_dense_sections += 1

    admin_token_ratio = round(total_admin_hits / max(1, total_tokens), 3)
    reasons: list[str] = []
    if len(missing_sections) >= 3:
        reasons.append("too_many_missing_key_sections")
    if admin_dominant_sections or admin_token_ratio >= 0.12:
        reasons.append("boilerplate_dominant_sections")
    if mixed_sections:
        reasons.append("mixed_section_content")
    if clinically_dense_sections < 3:
        reasons.append("clinical_information_density_low")
    return {
        "passed": not reasons,
        "missing_key_sections": missing_sections,
        "admin_dominant_sections": sorted(set(admin_dominant_sections)),
        "mixed_sections": sorted(set(mixed_sections)),
        "clinically_dense_sections": clinically_dense_sections,
        "admin_token_ratio": admin_token_ratio,
        "reasons": reasons,
    }


@dataclass
class SupervisorResult:
    summary: Dict[str, Any]
    validation: Dict[str, Any]


class CommonSenseSupervisor:
    """Supervisor that validates summaries against coarse expectations."""

    required_header_tokens: Sequence[str] = _HEADER_TOKENS  # pragma: no cover

    stopwords: frozenset[str] = _STOPWORDS  # pragma: no cover

    def __init__(
        self,
        *,
        simple: bool = False,
        min_ratio: float = 0.01,
        baseline_min_chars: int = 200,
        multi_pass_min_chars: int = 600,
        max_retries: int = 3,
        logger: logging.Logger | None = None,
    ) -> None:
        self.simple = simple
        self.min_ratio = min_ratio  # pragma: no cover - constructor wiring
        self.baseline_min_chars = (
            baseline_min_chars  # pragma: no cover - constructor wiring
        )
        self.multi_pass_min_chars = (
            multi_pass_min_chars  # pragma: no cover - constructor wiring
        )
        self.max_retries = max_retries  # pragma: no cover - constructor wiring
        self.logger = logger or _LOG  # pragma: no cover - constructor wiring
        self._retry_variants: Tuple[Dict[str, Any], ...] = (
            {"name": "chunk-tight", "chunk_target": 2000, "chunk_max": 2600},
            {"name": "chunk-balanced", "chunk_target": 1600, "chunk_max": 2200},
            {"name": "chunk-dense", "chunk_target": 1200, "chunk_max": 1800},
        )

    # ------------------------------------------------------------------
    # Pre-processing helpers
    # ------------------------------------------------------------------
    def collect_doc_stats(
        self,
        *,
        text: str,
        pages: Sequence[Any] | None,
        file_bytes: bytes | bytearray | None,
    ) -> Dict[str, Any]:
        page_count = len(pages) if pages else 0
        text_length = len(text or "")
        size_mb = _normalise_mb(len(file_bytes) if file_bytes else None)
        return {
            "pages": page_count,
            "text_length": text_length,
            "file_size_mb": size_mb,
        }

    # ------------------------------------------------------------------
    # Validation core
    # ------------------------------------------------------------------
    def validate(
        self,
        *,
        ocr_text: str,
        alignment_source_text: str | None = None,
        summary: Dict[str, Any],
        doc_stats: Dict[str, Any],
        retries: int = 0,
        attempt_label: str | None = None,
    ) -> Dict[str, Any]:
        if self.simple:
            ocr_text_normalised = (ocr_text or "").strip()
            text_len = len(ocr_text_normalised)
            summary_payload: Dict[str, Any]
            if isinstance(summary, SummaryContract):
                summary_payload = summary.to_dict()
            elif isinstance(summary, dict):
                summary_payload = summary
            else:
                summary_payload = {"Medical Summary": str(summary or "")}
            summary_text = _extract_summary_text(summary_payload).strip()
            summary_chars = len(summary_text)
            ratio = summary_chars / max(1, text_len)
            min_summary_default = str(max(self.baseline_min_chars, 300))
            min_summary_chars = int(os.getenv("MIN_SUMMARY_CHARS", min_summary_default))
            min_ratio = float(os.getenv("MIN_SUMMARY_RATIO", "0.005"))
            summary_lc = summary_text.lower()
            keyword_hits = sum(
                summary_lc.count(token) for token in _INVALID_SUMMARY_KEYWORDS
            )
            quality = _assess_structured_summary_quality(summary_payload)
            head_fragment = summary_text.strip()[:32]
            looks_placeholder = bool(_PLACEHOLDER_RE.fullmatch(head_fragment))
            too_short = summary_chars < min_summary_chars
            ratio_ok = ratio >= min_ratio if text_len else False
            semantic_ok = keyword_hits < 2 and not looks_placeholder
            checks = {
                "length_ok": not too_short,
                "semantic_ok": semantic_ok,
                "ratio_ok": ratio_ok,
                "structure_ok": semantic_ok,
                "alignment_ok": True,
                "quality_ok": quality["passed"],
                "multi_pass_required": False,
                "paragraphs": 1 if summary_chars else 0,
                "headers": 0,
            }
            passed = all(
                checks[key]
                for key in ("length_ok", "semantic_ok", "ratio_ok", "quality_ok")
            )
            failure_reasons = [
                key
                for key in ("length_ok", "semantic_ok", "ratio_ok")
                if not checks[key]
            ]
            if not checks["quality_ok"]:
                failure_reasons.append("summary_quality_low")
            reason = "" if passed else ",".join(failure_reasons) or "failed_checks"
            validation = {
                "supervisor_passed": passed,
                "retries": retries,
                "reason": reason,
                "length_score": round(ratio, 3),
                "content_alignment": 1.0,
                "doc_stats": {
                    "pages": int(doc_stats.get("pages") or 0),
                    "text_length": int(doc_stats.get("text_length") or text_len),
                    "file_size_mb": float(doc_stats.get("file_size_mb") or 0.0),
                },
                "checks": checks,
                "quality": quality,
            }
            log_extra = {
                "length_score": validation["length_score"],
                "summary_chars": summary_chars,
            }
            if passed:
                self.logger.info("supervisor_simple_passed", extra=log_extra)
            else:
                log_extra.update(
                    {
                        "reason": reason,
                        "keyword_hits": keyword_hits,
                        "placeholder": looks_placeholder,
                        "min_required": min_summary_chars,
                        "quality_reasons": quality["reasons"],
                    }
                )
                self.logger.warning("supervisor_simple_flagged", extra=log_extra)
            return validation
        self.logger.info(
            "supervisor_validation_started",
            extra={
                "pages": doc_stats.get("pages"),
                "text_length": doc_stats.get("text_length"),
                "retries": retries,
                "attempt": attempt_label or "initial",
            },
        )
        summary_text = _extract_summary_text(summary)
        alignment_focus_text = _strip_section_headers(summary_text)
        summary_chars = len(summary_text.strip())
        require_multi_pass = bool(
            (doc_stats.get("pages") or 0) > 50
            or (doc_stats.get("text_length") or 0) > 100_000
        )

        target_length = max(
            int((doc_stats.get("text_length") or 0) * self.min_ratio),
            (
                self.multi_pass_min_chars
                if require_multi_pass
                else self.baseline_min_chars
            ),
        )
        length_score = 0.0
        if target_length <= 0:
            length_score = 1.0
        else:
            length_score = min(summary_chars / target_length, 1.0)

        paragraph_count = self._count_paragraphs(summary_text)
        header_count = self._count_headers(summary_text)
        has_list = self._has_structured_list(summary_text, summary)
        quality = _assess_structured_summary_quality(summary)
        alignment_input = alignment_source_text if alignment_source_text is not None else ocr_text
        alignment_source = clean_ocr_output(alignment_input) or alignment_input
        alignment = self._content_alignment(alignment_source, alignment_focus_text)
        source_token_count = sum(1 for _ in self._tokenize(alignment_source))
        summary_token_count = sum(1 for _ in self._tokenize(alignment_focus_text))
        source_paragraph_count = self._count_paragraphs(alignment_input)
        summary_section_count = 0
        if isinstance(summary, dict):
            sections = summary.get("sections")
            if isinstance(sections, list):
                summary_section_count = len(sections)
        self.logger.info(
            "supervisor_alignment_metrics",
            extra={
                "attempt": attempt_label or "initial",
                "retries": retries,
                "content_alignment": round(alignment, 3),
                "source_paragraphs": source_paragraph_count,
                "summary_paragraphs": paragraph_count,
                "source_tokens": source_token_count,
                "summary_tokens": summary_token_count,
                "summary_headers": header_count,
                "summary_sections": summary_section_count,
            },
        )

        # Runtime tunable thresholds (env overrides) for adaptive strictness
        alignment_threshold = float(os.getenv("SUPERVISOR_ALIGNMENT_THRESHOLD", "0.80"))
        length_threshold = float(os.getenv("SUPERVISOR_LENGTH_SCORE_THRESHOLD", "0.75"))
        min_headers_required = int(os.getenv("SUPERVISOR_MIN_HEADERS", "3"))
        require_list = os.getenv("SUPERVISOR_REQUIRE_LIST", "true").lower() == "true"

        length_ok = length_score >= length_threshold
        structure_ok = header_count >= min_headers_required and (
            has_list or not require_list
        )
        if (doc_stats.get("pages") or 0) >= 100:
            structure_ok = structure_ok and (
                paragraph_count >= 3 or summary_chars >= 1000
            )

        alignment_ok = alignment >= alignment_threshold
        quality_ok = quality["passed"]

        passed = length_ok and structure_ok and alignment_ok and quality_ok
        reasons: list[str] = []
        if not length_ok:
            reasons.append("length_below_threshold")
        if not structure_ok:
            reasons.append("structure_requirements_not_met")
        if not alignment_ok:
            reasons.append("content_alignment_low")
        if not quality_ok:
            reasons.append("summary_quality_low")

        validation = {
            "supervisor_passed": passed,
            "retries": retries,
            "reason": ",".join(reasons) if reasons else "",
            "length_score": round(length_score, 3),
            "content_alignment": round(alignment, 3),
            "doc_stats": {  # pragma: no cover - aggregation plumbing
                "pages": int(doc_stats.get("pages") or 0),
                "text_length": int(doc_stats.get("text_length") or 0),
                "file_size_mb": float(doc_stats.get("file_size_mb") or 0.0),
            },
            "checks": {
                "length_ok": length_ok,
                "structure_ok": structure_ok,
                "alignment_ok": alignment_ok,
                "quality_ok": quality_ok,
                "multi_pass_required": require_multi_pass,
                "paragraphs": paragraph_count,
                "headers": header_count,
            },
            "quality": quality,
        }

        if passed:  # pragma: no cover - logging only
            self.logger.info(
                "supervisor_passed",
                extra={
                    "length_score": validation["length_score"],
                    "content_alignment": validation["content_alignment"],
                    "retries": retries,
                },
            )
        else:
            self.logger.warning(
                "supervisor_flagged",
                extra={
                    "reason": validation["reason"],
                    "retries": retries,
                    "length_score": validation["length_score"],
                    "content_alignment": validation["content_alignment"],
                },
            )
        return validation

    # ------------------------------------------------------------------
    # Retry and merge orchestration
    # ------------------------------------------------------------------
    def retry_and_merge(
        self,
        *,
        summariser: Any,
        ocr_text: str,
        alignment_source_text: str | None = None,
        doc_stats: Dict[str, Any],
        initial_summary: Dict[str, Any],
        initial_validation: Dict[str, Any],
        doc_metadata: Dict[str, Any] | None = None,
    ) -> SupervisorResult:
        if self.simple:
            return SupervisorResult(initial_summary, initial_validation)
        best_summary = initial_summary
        best_validation = dict(initial_validation)
        best_score = self._score(initial_validation)
        if initial_validation.get("supervisor_passed"):
            return SupervisorResult(best_summary, best_validation)

        retries_used = 0
        for variant in self._retry_variants[: self.max_retries]:
            retries_used += 1
            self.logger.info(
                "supervisor_retry",
                extra={
                    "attempt": retries_used,
                    "variant": variant["name"],
                    "pages": doc_stats.get("pages"),
                    "text_length": doc_stats.get("text_length"),
                },
            )
            candidate_summary = self._invoke_variant(
                summariser,
                ocr_text,
                variant,
                doc_metadata=doc_metadata,
            )
            candidate_validation = self.validate(
                ocr_text=ocr_text,
                alignment_source_text=alignment_source_text,
                summary=candidate_summary,
                doc_stats=doc_stats,
                retries=retries_used,
                attempt_label=variant["name"],
            )
            score = self._score(candidate_validation)
            if score > best_score:
                best_score = score
                best_summary = candidate_summary
                best_validation = candidate_validation
            if candidate_validation.get("supervisor_passed"):
                best_validation["retries"] = retries_used
                return SupervisorResult(candidate_summary, candidate_validation)

        best_validation["retries"] = retries_used
        return SupervisorResult(best_summary, best_validation)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _score(self, validation: Dict[str, Any]) -> float:
        length_score = float(validation.get("length_score") or 0.0)
        alignment = float(validation.get("content_alignment") or 0.0)
        return round(0.4 * length_score + 0.6 * alignment, 3)

    def _invoke_variant(
        self,
        summariser: Any,
        text: str,
        variant: Dict[str, Any],
        *,
        doc_metadata: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        chunk_target_attr = getattr(summariser, "chunk_target_chars", None)
        chunk_max_attr = getattr(summariser, "chunk_hard_max", None)
        restore_target = chunk_target_attr
        restore_max = chunk_max_attr
        try:
            if chunk_target_attr is not None and variant.get("chunk_target"):
                setattr(summariser, "chunk_target_chars", variant["chunk_target"])
            if chunk_max_attr is not None and variant.get("chunk_max"):
                setattr(summariser, "chunk_hard_max", variant["chunk_max"])
            # Preserve paragraph/layout cues for retry attempts; the refactored
            # summariser performs its own OCR cleaning internally.
            summary_input = text
            summarise = getattr(summariser, "summarise")
            if doc_metadata is not None:
                supports_doc_metadata = False
                try:
                    supports_doc_metadata = (
                        "doc_metadata" in inspect.signature(summarise).parameters
                    )
                except (TypeError, ValueError):
                    supports_doc_metadata = False
                if supports_doc_metadata:
                    return summarise(summary_input, doc_metadata=doc_metadata)
            return summarise(summary_input)
        finally:
            if restore_target is not None:
                setattr(summariser, "chunk_target_chars", restore_target)
            if restore_max is not None:
                setattr(summariser, "chunk_hard_max", restore_max)

    def _count_paragraphs(self, summary_text: str) -> int:
        if not summary_text:
            return 0
        paras = [p.strip() for p in summary_text.split("\n\n") if p.strip()]
        if not paras and summary_text.strip():
            return 1
        return len(paras)

    def _count_headers(self, summary_text: str) -> int:
        if not summary_text:
            return 0
        lowered = summary_text.lower()
        return len({token for token in self.required_header_tokens if token in lowered})

    def _has_structured_list(self, summary_text: str, summary: Dict[str, Any]) -> bool:
        if summary_text and re.search(r"\n\s*[-*]\s+", summary_text):
            return True
        if summary_text and re.search(r"\n\s*\d+\.\s+", summary_text):
            return True
        for key in ("_diagnoses_list", "_providers_list", "_medications_list"):
            value = summary.get(key)
            if isinstance(value, str) and value.strip():
                return True
        return False

    def _tokenize(self, text: str) -> Iterable[str]:
        for match in _WORD_RE.finditer(text or ""):
            token = match.group(0).lower()
            if token in self.stopwords:
                continue
            if token.isdigit():
                continue
            yield token

    def _top_phrases(self, tokens: Iterable[str], limit: int = 40) -> Sequence[str]:
        counter: Counter[str] = Counter()
        token_list = list(tokens)
        for token in token_list:
            counter[token] += 1
        for a, b in zip(token_list, token_list[1:]):
            if a == b:
                continue
            counter[f"{a} {b}"] += 1
        if not counter:
            return []
        most_common = counter.most_common(limit)
        return [item for item, _ in most_common]

    def _token_overlap(self, source_text: str, summary_text: str) -> float:
        source_tokens = set(self._tokenize(source_text))
        summary_tokens = set(self._tokenize(summary_text))
        if not source_tokens or not summary_tokens:
            return 0.0
        overlap = len(source_tokens & summary_tokens)
        denominator = max(1, min(len(source_tokens), len(summary_tokens)))
        return round(overlap / denominator, 3)

    def _content_alignment(self, source_text: str, summary_text: str) -> float:
        token_overlap = self._token_overlap(source_text, summary_text)
        source_tokens = list(self._tokenize(source_text))
        summary_tokens = list(self._tokenize(summary_text))
        if not source_tokens or not summary_tokens:
            return 0.0
        source_phrases = self._top_phrases(
            source_tokens, limit=_ALIGNMENT_PHRASE_LIMIT
        )
        summary_phrases = self._top_phrases(
            summary_tokens, limit=_ALIGNMENT_PHRASE_LIMIT
        )
        if not summary_phrases or not source_phrases:
            return token_overlap
        source_set = set(source_phrases)
        overlap = sum(1 for phrase in summary_phrases if phrase in source_set)
        denominator = max(1, min(len(summary_phrases), len(source_phrases)))
        phrase_overlap = overlap / denominator
        return round((0.55 * phrase_overlap) + (0.45 * token_overlap), 3)


__all__ = ["CommonSenseSupervisor", "SupervisorResult"]
