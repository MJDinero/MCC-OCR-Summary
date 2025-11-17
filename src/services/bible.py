"""Canonical MCC Bible constants shared across the pipeline."""

from __future__ import annotations

from typing import Dict, Tuple

CANONICAL_NARRATIVE_ORDER: Tuple[str, ...] = (
    "Provider Seen",
    "Reason for Visit",
    "Clinical Findings",
    "Treatment / Follow-up Plan",
)

CANONICAL_ENTITY_ORDER: Tuple[str, ...] = (
    "Diagnoses",
    "Healthcare Providers",
    "Medications / Prescriptions",
)

CANONICAL_SECTION_ORDER: Tuple[str, ...] = CANONICAL_NARRATIVE_ORDER + CANONICAL_ENTITY_ORDER

CANONICAL_SECTION_CONFIG: Dict[str, Dict[str, object]] = {
    "Provider Seen": {
        "key": "provider_seen",
        "bullet": False,
        "fallback": "No provider was referenced in the record.",
    },
    "Reason for Visit": {
        "key": "reason_for_visit",
        "bullet": True,
        "fallback": "Reason for visit was not documented.",
    },
    "Clinical Findings": {
        "key": "clinical_findings",
        "bullet": True,
        "fallback": "No clinical findings were highlighted.",
    },
    "Treatment / Follow-up Plan": {
        "key": "treatment_follow_up_plan",
        "bullet": True,
        "fallback": "No follow-up plan was identified.",
    },
    "Diagnoses": {
        "key": "_diagnoses_list",
        "bullet": True,
        "fallback": "Not explicitly documented.",
    },
    "Healthcare Providers": {
        "key": "_providers_list",
        "bullet": True,
        "fallback": "Not listed.",
    },
    "Medications / Prescriptions": {
        "key": "_medications_list",
        "bullet": True,
        "fallback": "No medications recorded in extracted text.",
    },
}

CANONICAL_PDF_STRUCTURE: Tuple[Tuple[str, bool], ...] = tuple(
    (heading, bool(CANONICAL_SECTION_CONFIG[heading]["bullet"]))
    for heading in CANONICAL_SECTION_ORDER
)

CANONICAL_FALLBACKS: Dict[str, str] = {
    heading: str(cfg["fallback"]) for heading, cfg in CANONICAL_SECTION_CONFIG.items()
}

CANONICAL_SUMMARY_KEYS: Dict[str, str] = {
    heading: str(cfg["key"]) for heading, cfg in CANONICAL_SECTION_CONFIG.items()
}

FORBIDDEN_PDF_PHRASES: Tuple[str, ...] = (
    "(condensed)",
    "structured indices",
    "summary lists",
    "summary notes",
    "document processed in",
    "female patients pregnancy",
    "please fill your prescriptions",
    "i understand that",
    "drinks caffeine",
    "any alcohol",
    "tell a health care provider",
    "please inform the staff and provider",
    "if you suspect you are pregnant",
    "your health care provider may",
)

ENTITY_FORBIDDEN_TOKENS: Tuple[str, ...] = (
    "document",
    "page",
    "instructions",
    "summary",
    "consent",
)

__all__ = [
    "CANONICAL_NARRATIVE_ORDER",
    "CANONICAL_ENTITY_ORDER",
    "CANONICAL_SECTION_ORDER",
    "CANONICAL_SECTION_CONFIG",
    "CANONICAL_PDF_STRUCTURE",
    "CANONICAL_FALLBACKS",
    "CANONICAL_SUMMARY_KEYS",
    "FORBIDDEN_PDF_PHRASES",
    "ENTITY_FORBIDDEN_TOKENS",
]
