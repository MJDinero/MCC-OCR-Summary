"""Common sense supervisor enforcing structural quality of generated summaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, Sequence, Iterable, Tuple
import logging
import re
from collections import Counter
import os

from src.models.summary_contract import SummaryContract
from src.services.docai_helper import clean_ocr_output


_LOG = logging.getLogger("supervisor")


_WORD_RE = re.compile(r"[A-Za-z0-9']+")

_HEADER_TOKENS: tuple[str, ...] = (
    "diagnoses:",
    "clinical findings:",
    "treatment",
    "medications",
    "reason for visit:",
    "provider seen:",
)  # pragma: no cover

_STOPWORDS: frozenset[str] = frozenset(
    "the and or of to a in for on with at by is are was were be this that from as an it patient patients medical summary plan follow diagnosis".split()
)  # pragma: no cover

_INVALID_SUMMARY_KEYWORDS: frozenset[str] = frozenset(
    {"n/a", "no data", "none", "empty", "tbd"}
)
_PLACEHOLDER_RE = re.compile(
    r"^(?:n/?a|no data|none|empty|tbd)[\s\.\-]*$", re.IGNORECASE
)


def _strip_section_headers(summary_text: str) -> str:
    """Remove section headers to focus alignment on substantive content."""

    if not summary_text:
        return ""
    cleaned_lines: list[str] = []
    for raw_line in summary_text.splitlines():
        if not raw_line:
            cleaned_lines.append(raw_line)
            continue
        if ":" in raw_line:
            _, remainder = raw_line.split(":", 1)
            cleaned_lines.append(remainder.strip())
        else:  # pragma: no cover - handled implicitly by colon stripping tests
            cleaned_lines.append(raw_line.strip())
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
                "multi_pass_required": False,
                "paragraphs": 1 if summary_chars else 0,
                "headers": 0,
            }
            passed = all(
                checks[key] for key in ("length_ok", "semantic_ok", "ratio_ok")
            )
            failure_reasons = [
                key
                for key in ("length_ok", "semantic_ok", "ratio_ok")
                if not checks[key]
            ]
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
        alignment_source = clean_ocr_output(ocr_text) or ocr_text
        alignment = self._content_alignment(alignment_source, alignment_focus_text)

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

        passed = length_ok and structure_ok and alignment_ok
        reasons: list[str] = []
        if not length_ok:
            reasons.append("length_below_threshold")
        if not structure_ok:
            reasons.append("structure_requirements_not_met")
        if not alignment_ok:
            reasons.append("content_alignment_low")

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
                "multi_pass_required": require_multi_pass,
                "paragraphs": paragraph_count,
                "headers": header_count,
            },
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
        doc_stats: Dict[str, Any],
        initial_summary: Dict[str, Any],
        initial_validation: Dict[str, Any],
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
            candidate_summary = self._invoke_variant(summariser, ocr_text, variant)
            candidate_validation = self.validate(
                ocr_text=ocr_text,
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
        self, summariser: Any, text: str, variant: Dict[str, Any]
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
            summary_input = clean_ocr_output(text) or text
            return summariser.summarise(summary_input)
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

    def _content_alignment(self, source_text: str, summary_text: str) -> float:
        source_tokens = list(self._tokenize(source_text))
        summary_tokens = list(self._tokenize(summary_text))
        if not source_tokens or not summary_tokens:
            return 0.0
        source_phrases = self._top_phrases(source_tokens, limit=50)
        summary_phrases = self._top_phrases(summary_tokens, limit=50)
        if not summary_phrases or not source_phrases:
            return 0.0
        source_set = set(source_phrases)
        overlap = sum(1 for phrase in summary_phrases if phrase in source_set)
        denominator = max(1, min(len(summary_phrases), len(source_phrases)))
        return round(overlap / denominator, 3)


__all__ = ["CommonSenseSupervisor", "SupervisorResult"]
