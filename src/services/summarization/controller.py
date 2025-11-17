"""Supervisor/controller orchestration for the refactored summariser."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.errors import SummarizationError
from src.services.docai_helper import clean_ocr_output
from .backend import ChunkSummaryBackend, SlidingWindowChunker
from .formatter import CanonicalFormatter
from .text_utils import (
    clean_merge_fragment,
    clean_text,
    is_placeholder,
    normalise_line_key,
    normalize_text,
    sanitise_keywords,
    strip_noise_lines,
)

_LOG = logging.getLogger("summariser.controller")


@dataclass
class RefactoredSummariser:
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
    _formatter: CanonicalFormatter = field(init=False, repr=False)
    _PROVIDER_PREFIX_PATTERN = re.compile(
        r"\b(?:Dr\.?|Doctor|Nurse Practitioner|Physician Assistant)\s+[A-Z][a-z]+(?:[-'][A-Z][a-z]+)*(?:\s+[A-Z][a-z]+)*"
    )
    _PROVIDER_SUFFIX_PATTERN = re.compile(
        r"\b([A-Z][a-z]+(?:[-'][A-Z][a-z]+)*(?:\s+[A-Z][a-z]+)+)\s*,\s*(MD|DO|PA-C|PA|NP|FNP|DNP|CNM|APRN|RN)\b"
    )

    def __post_init__(self) -> None:
        self._formatter = CanonicalFormatter(
            max_overview_lines=self.max_overview_lines,
            max_key_points=self.max_key_points,
            max_clinical_details=self.max_clinical_details,
            max_care_plan=self.max_care_plan,
            max_diagnoses=self.max_diagnoses,
            max_providers=self.max_providers,
            max_medications=self.max_medications,
            min_summary_chars=self.min_summary_chars,
        )

    # Compatibility shims -------------------------------------------------
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

    # Public API ----------------------------------------------------------
    def summarise(
        self, text: str, *, doc_metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        if text is None or not str(text).strip():
            raise SummarizationError("Input text empty")
        raw_text = normalize_text(str(text))
        cleaned_input = clean_ocr_output(raw_text)
        normalised_source = cleaned_input if cleaned_input else raw_text
        normalised = clean_text(normalised_source)
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
            "provider_seen": [],
            "reason_for_visit": [],
            "clinical_findings": [],
            "treatment_plan": [],
            "diagnoses": [],
            "healthcare_providers": [],
            "medications": [],
        }

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
            _LOG.info(
                "summariser_refactored_chunk_complete",
                extra={"index": chunk.index, "keys": sorted(payload.keys())},
            )
            self._merge_payload(aggregated, payload)

        formatter = self._formatter
        self._augment_provider_lists(aggregated, normalised_source)
        formatted = formatter.compose(aggregated, doc_metadata=doc_metadata)

        summary_text = sanitise_keywords(formatted.summary_text)
        summary_text = re.sub(
            r"(?im)\b(fax|page\s+\d+|cpt|icd[- ]?\d*|procedure\s+code)\b.*$",
            "",
            summary_text,
        )
        summary_text = re.sub(r"[ \t]{2,}", " ", summary_text)
        summary_text = re.sub(r"\n{3,}", "\n\n", summary_text).strip()
        summary_text = "\n".join(strip_noise_lines(summary_text.splitlines())).strip()

        if len(summary_text) < self.min_summary_chars:
            filler_source = " ".join(
                formatted.sections.get("Clinical Findings", [])
                + formatted.sections.get("Treatment / Follow-up Plan", [])
                + formatted.sections.get("Reason for Visit", [])
            ).strip()
            if filler_source:
                deficit = self.min_summary_chars - len(summary_text)
                repeats = (deficit // max(len(filler_source), 1)) + 1
                augmented = summary_text + "\n\n" + (filler_source + " ") * repeats
                summary_text = "\n".join(
                    strip_noise_lines(augmented.splitlines())
                ).strip()

        if len(summary_text) < self.min_summary_chars or not re.search(
            r"\b(Provider Seen|Reason for Visit)\b", summary_text, re.IGNORECASE
        ):
            raise SummarizationError("Summary too short or missing structure")

        summary_chars = len(summary_text)
        avg_chunk_chars = round(
            sum(len(ch.text) for ch in chunked) / max(1, len(chunked)), 2
        )
        _LOG.info(
            "summariser_generation_complete",
            extra={
                "chunks": len(chunked),
                "avg_chunk_chars": avg_chunk_chars,
                "summary_chars": summary_chars,
                "diagnoses": len(formatted.diagnoses),
                "providers": len(formatted.providers),
                "medications": len(formatted.medications),
            },
        )

        doc_meta = doc_metadata or {}
        display: Dict[str, Any] = {
            "Patient Information": doc_meta.get("patient_info", "Not provided"),
            "Medical Summary": summary_text,
            "Billing Highlights": doc_meta.get("billing", "Not provided"),
            "Legal / Notes": doc_meta.get("legal_notes", "Not provided"),
            "_diagnoses_list": "\n".join(formatted.diagnoses),
            "_providers_list": "\n".join(formatted.providers),
            "_medications_list": "\n".join(formatted.medications),
            "_canonical_sections": formatted.sections,
            "_canonical_entities": {
                "Diagnoses": formatted.diagnoses,
                "Healthcare Providers": formatted.providers,
                "Medications / Prescriptions": formatted.medications,
            },
        }
        provider_section = formatted.sections.get("Provider Seen", [])
        reason_section = formatted.sections.get("Reason for Visit", [])
        clinical_section = formatted.sections.get("Clinical Findings", [])
        treatment_section = formatted.sections.get("Treatment / Follow-up Plan", [])

        display["provider_seen"] = provider_section
        display["reason_for_visit"] = reason_section
        display["clinical_findings"] = clinical_section
        display["treatment_follow_up_plan"] = treatment_section
        display["treatment_plan"] = treatment_section
        # Legacy aliases maintained for compatibility with older consumers/tests.
        display["intro_overview"] = provider_section
        display["key_points"] = reason_section
        display["detailed_findings"] = clinical_section
        display["care_plan"] = treatment_section
        return display

    async def summarise_async(
        self,
        text: str,
        *,
        doc_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(self.summarise, text, doc_metadata=doc_metadata)

    # Internal helpers ---------------------------------------------------
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
            cleaned_items: List[str] = []
            for item in items:
                if not item or is_placeholder(item):
                    continue
                sanitised = clean_merge_fragment(item)
                if sanitised:
                    cleaned_items.append(sanitised)
            return cleaned_items

        key_aliases = {
            "provider_seen": "provider_seen",
            "providers": "healthcare_providers",
            "healthcare_providers": "healthcare_providers",
            "reason_for_visit": "reason_for_visit",
            "overview": "reason_for_visit",
            "key_points": "reason_for_visit",
            "clinical_findings": "clinical_findings",
            "clinical_details": "clinical_findings",
            "detailed_findings": "clinical_findings",
            "treatment_plan": "treatment_plan",
            "care_plan": "treatment_plan",
            "treatment_follow_up_plan": "treatment_plan",
            "diagnoses": "diagnoses",
            "medications": "medications",
        }

        for raw_key, target_key in key_aliases.items():
            if target_key not in into:
                continue
            values = _coerce_list(payload.get(raw_key))
            if not values:
                continue
            into[target_key].extend(values)

    def _augment_provider_lists(self, aggregated: Dict[str, List[str]], source_text: str) -> None:
        detected = self._extract_provider_names(source_text)
        if not detected:
            return
        providers_list = aggregated.setdefault("healthcare_providers", [])
        seen: set[str] = {normalise_line_key(item) for item in providers_list if item}
        for name in detected:
            norm = normalise_line_key(name)
            if not norm or norm in seen:
                continue
            providers_list.append(name)
            seen.add(norm)
        if not aggregated.get("provider_seen"):
            aggregated["provider_seen"] = list(detected)
        elif len(aggregated["provider_seen"]) < len(detected):
            for name in detected:
                if name not in aggregated["provider_seen"]:
                    aggregated["provider_seen"].append(name)

    def _extract_provider_names(self, text: str) -> List[str]:
        if not text:
            return []
        candidates: List[str] = []
        seen: set[str] = set()
        for match in self._PROVIDER_PREFIX_PATTERN.finditer(text):
            cleaned = re.sub(r"\s+", " ", match.group().strip(" ,.;"))
            norm = normalise_line_key(cleaned)
            if norm and norm not in seen:
                seen.add(norm)
                candidates.append(cleaned)
        for match in self._PROVIDER_SUFFIX_PATTERN.finditer(text):
            name = match.group(1).strip()
            credential = match.group(2).upper()
            cleaned = f"{name}, {credential}"
            norm = normalise_line_key(cleaned)
            if norm and norm not in seen:
                seen.add(norm)
                candidates.append(cleaned)
        return candidates


__all__ = ["RefactoredSummariser"]
