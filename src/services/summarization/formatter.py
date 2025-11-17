"""Canonical summary formatter shared by the summariser and PDF pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.services.bible import (
    CANONICAL_FALLBACKS as _CANONICAL_FALLBACKS,
    CANONICAL_NARRATIVE_ORDER,
    CANONICAL_PDF_STRUCTURE,
)
from .text_utils import (
    _DETAIL_VITAL_RE,
    _VITAL_TOKENS,
    clean_merge_fragment,
    contains_noise_phrase,
    is_admin_noise,
    jaccard_similarity,
    limit_sentences,
    looks_all_caps,
    matches_noise_fragment,
    normalise_line_key,
    strip_chunk_metadata,
    strip_noise_lines,
    tokenize_for_similarity,
)
_INTRO_NOISE_TOKENS = (
    "health care provider",
    "medicines",
    "medication",
    "smoking",
    "alcohol",
    "caffeine",
    "stress",
    "urinary",
    "bladder",
    "social history",
    "patient noted",
    "patient did not",
)
_PROVIDER_NOISE_TOKENS = (
    "your health care provider",
    "patient ",
    "social history",
    "smoking",
    "alcohol",
    "caffeine",
    "stress",
    "urinary",
    "medication",
    "medicines",
    "procedure",
)


@dataclass(slots=True)
class CanonicalSummary:
    summary_text: str
    sections: Dict[str, List[str]]
    diagnoses: List[str]
    providers: List[str]
    medications: List[str]

    def as_payload_lists(self) -> Dict[str, List[str]]:
        return {
            "Provider Seen": self.sections.get("Provider Seen", []),
            "Reason for Visit": self.sections.get("Reason for Visit", []),
            "Clinical Findings": self.sections.get("Clinical Findings", []),
            "Treatment / Follow-up Plan": self.sections.get("Treatment / Follow-up Plan", []),
            "Diagnoses": list(self.diagnoses),
            "Healthcare Providers": list(self.providers),
            "Medications / Prescriptions": list(self.medications),
        }

    def as_pdf_sections(self) -> List[Tuple[str, List[str], bool]]:
        sections: List[Tuple[str, List[str], bool]] = []
        for heading, bullet in CANONICAL_PDF_STRUCTURE:
            if heading in CANONICAL_NARRATIVE_ORDER:
                lines = self.sections.get(heading, [])
            elif heading == "Diagnoses":
                lines = self.diagnoses
            elif heading == "Healthcare Providers":
                lines = self.providers
            else:
                lines = self.medications
            sections.append((heading, list(lines), bullet))
        return sections


class CanonicalFormatter:
    """Applies heuristics to aggregated chunk payloads."""

    def __init__(
        self,
        *,
        max_overview_lines: int = 4,
        max_key_points: int = 6,
        max_clinical_details: int = 12,
        max_care_plan: int = 8,
        max_diagnoses: int = 12,
        max_providers: int = 12,
        max_medications: int = 12,
        min_summary_chars: int = 500,
    ) -> None:
        self.max_overview_lines = max_overview_lines
        self.max_key_points = max_key_points
        self.max_clinical_details = max_clinical_details
        self.max_care_plan = max_care_plan
        self.max_diagnoses = max_diagnoses
        self.max_providers = max_providers
        self.max_medications = max_medications
        self.min_summary_chars = min_summary_chars

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
        "patient",
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
        "blood pressure",
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

    def compose(
        self,
        aggregated: Dict[str, List[str]],
        *,
        doc_metadata: Optional[Dict[str, Any]] = None,
    ) -> CanonicalSummary:
        diagnoses = self._clean_section_lines(
            self._dedupe_ordered(
                aggregated.get("diagnoses", []), limit=self.max_diagnoses
            )
        )
        providers = self._clean_section_lines(
            self._dedupe_ordered(
                aggregated.get("healthcare_providers", [])
                or aggregated.get("providers", []),
                limit=self.max_providers,
            )
        )
        medications = self._clean_section_lines(
            self._dedupe_ordered(
                aggregated.get("medications", []),
                limit=self.max_medications,
                allow_numeric=True,
            )
        )
        facility = (doc_metadata or {}).get("facility") if doc_metadata else None
        clinical_details = self._clean_section_lines(
            self._dedupe_ordered(
                aggregated.get("clinical_findings", []),
                limit=self.max_clinical_details,
                keywords=self._DETAIL_TOKENS,
                require_tokens=(
                    "exam",
                    "imaging",
                    "vital",
                    "mri",
                    "ct",
                    "scan",
                    "blood",
                    "pressure",
                    "range",
                    "finding",
                ),
            )
        )
        care_plan = self._clean_section_lines(
            self._dedupe_ordered(
                aggregated.get("treatment_plan", [])
                or aggregated.get("care_plan", []),
                limit=self.max_care_plan,
                keywords=self._PLAN_TOKENS,
                require_tokens=(
                    "follow",
                    "return",
                    "schedule",
                    "therapy",
                    "plan",
                    "monitor",
                ),
            )
        )

        providers_list = self._filter_providers(providers)

        provider_candidates = self._clean_section_lines(
            self._dedupe_ordered(
                aggregated.get("provider_seen", []),
                limit=max(2, self.max_key_points // 2 or 1),
                keywords=("dr", "doctor", "provider", "clinic", "hospital", "team"),
            )
        )
        filtered_providers = self._filter_intro_lines(provider_candidates)
        provider_seen = filtered_providers
        if not provider_seen and providers_list:
            provider_seen = [f"Primary provider: {providers_list[0]}"]
            if len(providers_list) > 1:
                supporting = ", ".join(providers_list[1:3])
                provider_seen.append(f"Supporting team: {supporting}")
        if facility:
            facility_line = limit_sentences(f"Facility: {facility}.", 1)
            if (
                facility_line
                and not contains_noise_phrase(facility_line)
                and not matches_noise_fragment(facility_line)
                and facility_line not in provider_seen
            ):
                provider_seen.insert(0, facility_line)
        provider_seen = strip_noise_lines(provider_seen)
        if not provider_seen:
            provider_seen = ["No provider was referenced in the record."]

        reason_lines = self._clean_section_lines(
            self._dedupe_ordered(
                aggregated.get("reason_for_visit", []),
                limit=self.max_overview_lines,
                keywords=self._KEY_POINT_TOKENS,
                require_tokens=(
                    "visit",
                    "complaint",
                    "follow-up",
                    "evaluation",
                    "consult",
                    "reason",
                    "patient",
                ),
            )
        )
        if not reason_lines:
            fallback_reason_source = clinical_details[:1] or care_plan[:1]
            if fallback_reason_source:
                fallback_reason = limit_sentences(fallback_reason_source[0], 2)
                if fallback_reason:
                    reason_lines = [fallback_reason]
        if not reason_lines:
            reason_lines = ["Reason for visit was not documented."]

        detail_candidates = clinical_details or reason_lines
        detail_lines = self._filter_details(detail_candidates)
        if not detail_lines and detail_candidates:
            fallback_detail = limit_sentences(detail_candidates[0], 2)
            if fallback_detail:
                detail_lines = [fallback_detail]
        if not detail_lines:
            detail_lines = ["No detailed findings were highlighted."]

        care_candidates = care_plan or clinical_details or reason_lines
        care_lines = self._filter_care_plan(care_candidates)
        if not care_lines and care_candidates:
            fallback_care = limit_sentences(care_candidates[-1], 2)
            if fallback_care:
                care_lines = [fallback_care]
        if not care_lines:
            care_lines = ["No follow-up plan was identified."]

        diagnoses_list = self._filter_diagnoses(diagnoses)
        medications_list = self._filter_medications(medications)

        narrative_sections = {
            "Provider Seen": provider_seen,
            "Reason for Visit": reason_lines,
            "Clinical Findings": detail_lines,
            "Treatment / Follow-up Plan": care_lines,
        }
        deduped_narratives = self._dedupe_cross_sections(narrative_sections)
        provider_seen = deduped_narratives["Provider Seen"]
        reason_lines = deduped_narratives["Reason for Visit"]
        detail_lines = deduped_narratives["Clinical Findings"]
        care_lines = deduped_narratives["Treatment / Follow-up Plan"]

        sections_payload: List[Tuple[str, List[str], bool, bool]] = [
            ("Provider Seen", provider_seen, False, True),
            ("Reason for Visit", reason_lines, True, True),
            ("Clinical Findings", detail_lines, True, True),
            ("Treatment / Follow-up Plan", care_lines, True, True),
            ("Diagnoses", diagnoses_list, True, False),
            ("Healthcare Providers", providers_list, True, False),
            ("Medications / Prescriptions", medications_list, True, False),
        ]
        deduped_sections = self._dedupe_across_sections(sections_payload)
        summary_lines: List[str] = []
        for header, lines, bullet, _ in deduped_sections:
            if summary_lines:
                summary_lines.append("")
            summary_lines.append(f"{header}:")
            if not lines:
                fallback = _CANONICAL_FALLBACKS.get(header, "Not documented.")
                summary_lines.append(f"- {fallback}" if bullet else fallback)
                continue
            for line in lines:
                summary_lines.append(f"- {line}" if bullet else line)

        summary_lines = strip_noise_lines(summary_lines)
        summary_text = "\n".join(summary_lines).strip()
        if len(summary_text) < self.min_summary_chars:
            supplemental_lines = [
                line
                for line in (detail_lines + care_lines + reason_lines + provider_seen)
                if line
                and not contains_noise_phrase(line)
                and not matches_noise_fragment(line)
            ]
            if supplemental_lines:
                needed = max(0, self.min_summary_chars - len(summary_text))
                filler_fragment = " ".join(supplemental_lines).strip()
                if filler_fragment:
                    repeats = (needed // max(len(filler_fragment), 1)) + 1
                    filler = (filler_fragment + " ") * repeats
                    augmented = summary_lines + ["", filler[: needed + 20]]
                    summary_lines = strip_noise_lines(augmented)
                    summary_text = "\n".join(summary_lines).strip()

        return CanonicalSummary(
            summary_text=summary_text,
            sections={
                "Provider Seen": provider_seen,
                "Reason for Visit": reason_lines,
                "Clinical Findings": detail_lines,
                "Treatment / Follow-up Plan": care_lines,
            },
            diagnoses=diagnoses_list,
            providers=providers_list,
            medications=medications_list,
        )

    # Helper filters ---------------------------------------------------
    def _clean_section_lines(self, lines: Iterable[str]) -> List[str]:
        cleaned: List[str] = []
        for line in lines:
            sanitised = clean_merge_fragment(line)
            if sanitised:
                cleaned.append(sanitised)
        return cleaned

    @classmethod
    def _line_score(cls, text: str, keywords: Iterable[str]) -> float:
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
    def _is_noise_line(cls, value: str, *, allow_numeric: bool = False) -> bool:
        stripped = value.strip()
        if not stripped:
            return True
        if stripped.count("=") >= 5:
            return True
        if len(stripped) > 340:
            return True
        low = stripped.lower()
        if contains_noise_phrase(stripped):
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
            norm_key = normalise_line_key(val_clean)
            if not norm_key:
                continue
            low = val_clean.lower()
            if cls._is_noise_line(val_clean, allow_numeric=allow_numeric):
                continue
            if any(tok in low for tok in cls._UNWANTED_TOKENS):
                continue
            if matches_noise_fragment(val_clean):
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

    def _filter_intro_lines(self, lines: Iterable[str]) -> List[str]:
        filtered: List[str] = []
        for line in lines:
            candidate = limit_sentences(strip_chunk_metadata(line), 2)
            text = clean_merge_fragment(candidate)
            if not text:
                continue
            low = text.lower()
            if is_admin_noise(text):
                continue
            if "supporting team" in low:
                continue
            if any(token in low for token in _VITAL_TOKENS):
                continue
            if any(token in low for token in _INTRO_NOISE_TOKENS):
                continue
            if matches_noise_fragment(text):
                continue
            filtered.append(text)
            if len(filtered) >= 3:
                break
        return filtered

    def _filter_key_points(self, lines: Iterable[str]) -> List[str]:
        filtered: List[str] = []
        for line in lines:
            candidate = limit_sentences(strip_chunk_metadata(line), 2)
            text = clean_merge_fragment(candidate)
            if not text:
                continue
            if is_admin_noise(text) or matches_noise_fragment(text):
                continue
            if looks_all_caps(text):
                continue
            filtered.append(text)
        return filtered

    def _filter_details(self, lines: Iterable[str]) -> List[str]:
        filtered: List[str] = []
        impressions: List[str] = []
        seen_keys: set[str] = set()
        vital_seen = False
        for line in lines:
            candidate = limit_sentences(strip_chunk_metadata(line), 2)
            text = clean_merge_fragment(candidate)
            if not text:
                continue
            if is_admin_noise(text) or matches_noise_fragment(text):
                continue
            norm_key = normalise_line_key(text)
            if not norm_key or norm_key in seen_keys:
                continue
            seen_keys.add(norm_key)
            low = text.lower()
            if _DETAIL_VITAL_RE.search(low):
                if vital_seen:
                    continue
                vital_seen = True
            if "impression" in low:
                impressions.append(text)
                continue
            filtered.append(text)
        if impressions:
            filtered = impressions[-1:] + filtered
        return filtered

    def _filter_care_plan(self, lines: Iterable[str]) -> List[str]:
        filtered: List[str] = []
        seen_keys: set[str] = set()
        for line in lines:
            candidate = limit_sentences(strip_chunk_metadata(line), 2)
            text = clean_merge_fragment(candidate)
            if not text:
                continue
            if is_admin_noise(text) or matches_noise_fragment(text):
                continue
            low = text.lower()
            if "thank you" in low or "contact information" in low:
                continue
            if "call" in low and "clinic" in low and "if" not in low:
                continue
            norm_key = normalise_line_key(text)
            if not norm_key or norm_key in seen_keys:
                continue
            seen_keys.add(norm_key)
            filtered.append(text)
        return filtered

    def _filter_diagnoses(self, items: Iterable[str]) -> List[str]:
        filtered: List[str] = []
        seen: set[str] = set()
        allowed = re.compile(r"^[a-z0-9 ,./()%-]+$", re.IGNORECASE)
        forbidden = re.compile(
            r"(consent|instruction|policy|education|insurance|privacy|percentage relief|call 911)",
            re.IGNORECASE,
        )
        for item in items:
            candidate = strip_chunk_metadata((item or "").strip("•- \t\r\n"))
            text = clean_merge_fragment(candidate)
            if not text:
                continue
            if matches_noise_fragment(text) or is_admin_noise(text):
                continue
            if forbidden.search(text):
                continue
            if len(text.split()) > 14:
                continue
            if not allowed.match(text):
                continue
            norm_key = normalise_line_key(text)
            if not norm_key or norm_key in seen:
                continue
            seen.add(norm_key)
            filtered.append(text)
        return filtered

    def _filter_providers(self, items: Iterable[str]) -> List[str]:
        filtered: List[str] = []
        seen: set[str] = set()
        provider_token = re.compile(
            r"\b(dr\.?|md|do|pa|np|rn|fnp|anp|cnp|dnp|facs|physician|surgeon|nurse practitioner|physician assistant)\b",
            re.IGNORECASE,
        )
        reject = re.compile(
            r"(department|clinic|center|hospital|facility|billing|insurance|policy|consent|phone|fax)",
            re.IGNORECASE,
        )
        for item in items:
            candidate = strip_chunk_metadata((item or "").strip("•- \t\r\n"))
            text = clean_merge_fragment(candidate)
            if not text:
                continue
            if matches_noise_fragment(text):
                continue
            if reject.search(text):
                continue
            low = text.lower()
            if any(token in low for token in _PROVIDER_NOISE_TOKENS):
                continue
            if not provider_token.search(text):
                continue
            norm_key = normalise_line_key(text)
            if not norm_key or norm_key in seen:
                continue
            seen.add(norm_key)
            filtered.append(text)
        return filtered

    def _filter_medications(self, items: Iterable[str]) -> List[str]:
        filtered: List[str] = []
        seen: set[str] = set()
        token = re.compile(
            r"\b(mg|mcg|ml|units?|tablet|tab|capsule|cap|dose|daily|bid|tid|qid|qhs|qam|qpm|prn|inhaler|spray|patch|cream|ointment|solution|suspension|drops|iv|po|im|subq|sc|sublingual)\b",
            re.IGNORECASE,
        )
        reject = re.compile(
            r"(refill|pharmacy only|consent|policy|education|percentage relief|instruction)",
            re.IGNORECASE,
        )
        for item in items:
            candidate = strip_chunk_metadata((item or "").strip("•- \t\r\n"))
            text = clean_merge_fragment(candidate)
            if not text:
                continue
            if matches_noise_fragment(text) or is_admin_noise(text):
                continue
            if reject.search(text):
                continue
            if not token.search(text):
                continue
            norm_key = normalise_line_key(text)
            if not norm_key or norm_key in seen:
                continue
            seen.add(norm_key)
            filtered.append(text)
        return filtered

    def _dedupe_cross_sections(
        self, sections: Dict[str, List[str]]
    ) -> Dict[str, List[str]]:
        seen: set[str] = set()
        ordered_headings = list(CANONICAL_NARRATIVE_ORDER)
        ordered_headings.extend(
            heading for heading in sections.keys() if heading not in ordered_headings
        )
        deduped: Dict[str, List[str]] = {}
        for heading in ordered_headings:
            lines = sections.get(heading, []) or []
            filtered: List[str] = []
            for line in lines:
                norm = normalise_line_key(line)
                if not norm or norm in seen:
                    continue
                seen.add(norm)
                filtered.append(line)
            deduped[heading] = filtered
        return deduped

    def _dedupe_across_sections(
        self,
        sections: List[tuple[str, List[str], bool, bool]],
    ) -> List[tuple[str, List[str], bool, bool]]:
        seen_tokens: List[set[str]] = []
        result: List[tuple[str, List[str], bool, bool]] = []
        for header, lines, bullet, narrative in sections:
            if not narrative:
                result.append((header, lines, bullet, narrative))
                continue
            filtered_lines: List[str] = []
            for line in lines:
                tokens = tokenize_for_similarity(line)
                if tokens and any(
                    jaccard_similarity(tokens, prior) >= 0.8 for prior in seen_tokens
                ):
                    continue
                filtered_lines.append(line)
                if tokens:
                    seen_tokens.append(tokens)
            result.append((header, filtered_lines, bullet, narrative))
        return result


def build_pdf_sections_from_payload(summary: Dict[str, Any]) -> List[Tuple[str, str]]:
    sections_map = summary.get("_canonical_sections")
    canonical_entities = summary.get("_canonical_entities")
    diagnoses_raw = None
    providers_raw = None
    meds_raw = None
    if isinstance(canonical_entities, dict):
        diagnoses_raw = canonical_entities.get("Diagnoses")
        providers_raw = canonical_entities.get("Healthcare Providers") or canonical_entities.get("Providers")
        meds_raw = canonical_entities.get("Medications / Prescriptions")

    resolved_sections: Dict[str, List[str]] = {}
    if isinstance(sections_map, dict):
        for key, value in sections_map.items():
            if isinstance(value, list):
                resolved_sections[key] = [clean_merge_fragment(v) for v in value if clean_merge_fragment(v)]
            elif isinstance(value, str):
                resolved_sections[key] = [
                    clean_merge_fragment(part)
                    for part in value.splitlines()
                    if clean_merge_fragment(part)
                ]
    else:
        # Backward compatibility: fall back to legacy string lists
        legacy_keys = {
            "Provider Seen": summary.get("provider_seen")
            or summary.get("intro_overview")
            or summary.get("overview"),
            "Reason for Visit": summary.get("reason_for_visit")
            or summary.get("key_points"),
            "Clinical Findings": summary.get("clinical_findings")
            or summary.get("detailed_findings")
            or summary.get("clinical_details"),
            "Treatment / Follow-up Plan": summary.get("treatment_follow_up_plan")
            or summary.get("care_plan"),
        }
        for heading, raw in legacy_keys.items():
            if raw is None:
                resolved_sections[heading] = []
                continue
            if isinstance(raw, str):
                pieces = [clean_merge_fragment(part) for part in raw.splitlines() if clean_merge_fragment(part)]
            else:
                pieces = [clean_merge_fragment(str(part)) for part in raw if clean_merge_fragment(str(part))]
            resolved_sections[heading] = [piece for piece in pieces if piece]

    entity_map = canonical_entities if isinstance(canonical_entities, dict) else {}
    diagnoses_lines = _coerce_lines(
        diagnoses_raw or summary.get("_diagnoses_list") or entity_map.get("Diagnoses")
    )
    providers_lines = _coerce_lines(
        providers_raw
        or summary.get("_providers_list")
        or entity_map.get("Healthcare Providers")
        or entity_map.get("Providers")
    )
    meds_lines = _coerce_lines(
        meds_raw
        or summary.get("_medications_list")
        or entity_map.get("Medications / Prescriptions")
    )

    pdf_sections: List[Tuple[str, str]] = []
    for heading, bullet in CANONICAL_PDF_STRUCTURE:
        if heading in CANONICAL_NARRATIVE_ORDER:
            lines = resolved_sections.get(heading, [])
        elif heading == "Diagnoses":
            lines = diagnoses_lines
        elif heading == "Healthcare Providers":
            lines = providers_lines
        else:
            lines = meds_lines
        body = _lines_to_body(lines, heading, bullet)
        pdf_sections.append((heading, body))
    return pdf_sections


def _coerce_lines(raw: Any) -> List[str]:
    if raw is None:
        return []
    result: List[str] = []
    if isinstance(raw, list):
        source = raw
    elif isinstance(raw, str):
        source = raw.splitlines()
    else:
        source = [raw]
    for item in source:
        text = clean_merge_fragment(str(item))
        if text:
            result.append(text)
    return result


def _lines_to_body(lines: Sequence[str], heading: str, bullet: bool) -> str:
    cleaned = [line for line in lines if line]
    if not cleaned:
        return _CANONICAL_FALLBACKS.get(heading, "Not documented.")
    if bullet:
        return "\n".join(f"- {line}" for line in cleaned)
    return "\n".join(cleaned)

__all__ = [
    "CanonicalFormatter",
    "CanonicalSummary",
    "build_pdf_sections_from_payload",
    "CANONICAL_PDF_STRUCTURE",
]
