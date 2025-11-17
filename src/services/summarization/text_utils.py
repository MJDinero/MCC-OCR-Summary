"""Shared text cleaning helpers for summarisation + PDF assembly."""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Set, Tuple

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE_RE = re.compile(r"[\s\u00a0]+")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_PLACEHOLDER_RE = re.compile(
    r"^(?:n/?a|none(?:\s+(?:noted|reported|recorded))?|no data|empty|tbd|not (?:applicable|documented|provided)|nil)$",
    re.IGNORECASE,
)
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
    "patient intake form",
    "intake paperwork",
    "intake questionnaire",
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
    "consent for treatment",
    "consent to treat",
    "consent agreement",
    "authorization to release medical records",
    "medical records release form",
    "potential for additional necessary care",
    "order status",
    "department status",
    "follow-up evaluation date",
    "worker's comp",
    "greater plains orthopedic",
    "tell a health care provider",
    "if you suspect you are pregnant",
    "please inform the staff and provider",
    "your health care provider may",
    "general instructions ask your health care provider",
    "please ask your physician",
    "before signing this form",
    "doctor excuse",
    "i voluntarily request my physician",
    "description of medical care and surgical procedure",
    "site will be free of infection",
    "observe for bleeding in excess",
    "ask your health care provider",
    "all medicines you are taking",
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_FINAL_NOISE_PATTERN_STRINGS: tuple[str, ...] = (
    r"temporary localized increase in pain",
    r"fever, facial flushing",
    r"call the office immediately",
    r"emergency room",
    r"patient education",
    r"discharge instructions",
    r"no heavy lifting",
    r"facet injections are mostly a diagnostic tool",
    r"medial branch blocks are spinal injections",
    r"i retain the right to refuse",
    r"plan of care \(continued\)",
    r"thank you for choosing",
    r"return to your normal activities",
    r"instructions, prescriptions",
    r"educated on care of site",
    r"workers? comp",
    r"potential for additional necessary care",
    r"order status",
    r"department status",
    r"follow-up evaluation date",
    r"document processed in \\d+\\s+chunk(?:s)?",
    r"female patients pregnancy",
    r"please fill your prescriptions",
    r"pharmacy only:",
    r"write the percentage relief",
    r"greater [\\w\\s]+ orthopedic",
    r"\\bi understand that\\b",
    r"patient (education|consent|privacy notice)",
    r"(?:patient|client)\\s+intake\\s+(?:form|packet|questionnaire|paperwork)",
    r"consent\\s+(?:form|document|paperwork|agreement)",
    r"(?:authorization|authorisation)\\s+(?:to|for)\\s+release",
    r"tell a health care provider",
    r"please inform the staff and provider",
    r"if you suspect you are pregnant",
    r"your health care provider may",
    r"general instructions\\s+ask your health care provider",
    r"please ask your physician",
    r"before signing this form",
    r"doctor\\s+excuse",
    r"return to work\\s*\\d",
    r"patient is free from signs and symptoms of injury",
    r"i voluntarily request my physician",
    r"description of medical care and surgical procedure",
    r"site will be free of infection",
    r"observe for bleeding in excess",
    r"ask your health care provider",
    r"all medicines you are taking",
    r"drinks caffeine",
    r"social history",
    r"review of systems",
)
_FINAL_NOISE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE) for pattern in _FINAL_NOISE_PATTERN_STRINGS
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CHUNK_METADATA_RE = re.compile(
    r"\\s*document processed in\\s+\\d+\\s+chunk\(s\)(?:\\.\\s*)?",
    re.IGNORECASE,
)
_ADMIN_KEYWORDS = (
    "consent",
    "privacy notice",
    "policy",
    "insurance",
    "billing",
    "payment",
    "signature",
    "percentage relief",
    "pharmacy only",
    "document processed",
    "refill request",
    "patient education",
    "intake form",
    "intake packet",
    "intake paperwork",
    "intake questionnaire",
    "consent form",
    "consent packet",
    "consent paperwork",
)
_VITAL_TOKENS = (
    "blood pressure",
    "vital",
    "pulse",
    "temperature",
    "heart rate",
    "respiratory rate",
    "oxygen saturation",
)
_DETAIL_VITAL_RE = re.compile(
    r"\\b(blood pressure|bp|pulse|heart rate|temperature|respiratory|oxygen saturation)\\b",
    re.IGNORECASE,
)

_FORM_LANGUAGE_KEYWORDS: Tuple[str, ...] = (
    "intake form",
    "intake packet",
    "intake paperwork",
    "intake questionnaire",
    "consent form",
    "consent paperwork",
    "consent packet",
    "consent agreement",
    "consent to treat",
    "authorization to release",
    "authorization for release",
    "medical records release",
)
_FORM_LANGUAGE_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:patient|client|injury)\s+intake\s+(?:form|forms|packet|paperwork|questionnaire)",
        re.IGNORECASE,
    ),
    re.compile(r"intake\s+(?:paperwork|packet|questionnaire|documents?)", re.IGNORECASE),
    re.compile(r"(?:treatment|procedure|surgery)\s+consent", re.IGNORECASE),
    re.compile(r"consent\s+(?:form|forms|document|documents|paperwork|agreement)", re.IGNORECASE),
    re.compile(
        r"(?:authorization|authorisation)\s+(?:form|forms|document|documents|paperwork)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:authorization|authorisation)\s+(?:to|for)\s+release",
        re.IGNORECASE,
    ),
)


def normalize_text(value: str) -> str:
    normalised = unicodedata.normalize("NFKC", value or "")
    try:
        ascii_normalised = normalised.encode("ascii", "ignore").decode("ascii")
    except Exception:
        ascii_normalised = normalised
    return ascii_normalised


def clean_text(raw: str) -> str:
    normalised = normalize_text(raw or "")
    cleaned = _CONTROL_CHARS_RE.sub(" ", normalised)
    collapsed = _WHITESPACE_RE.sub(" ", cleaned)
    return collapsed.strip()


def is_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER_RE.match(value.strip()))


def sanitise_keywords(text: str) -> str:
    cleaned = text
    for pattern, replacement in _KEYWORD_SANITISERS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


def contains_noise_phrase(value: str) -> bool:
    low = value.lower()
    return any(phrase in low for phrase in _LEGAL_NOISE_PHRASES)


def matches_noise_fragment(value: str) -> bool:
    if not value:
        return False
    return any(pattern.search(value) for pattern in _FINAL_NOISE_PATTERNS)


def contains_intake_form_language(value: str) -> bool:
    if not value:
        return False
    low = value.lower()
    if any(keyword in low for keyword in _FORM_LANGUAGE_KEYWORDS):
        return True
    return any(pattern.search(value) for pattern in _FORM_LANGUAGE_PATTERNS)


def strip_chunk_metadata(text: str) -> str:
    if not text:
        return ""
    cleaned = _CHUNK_METADATA_RE.sub(" ", text)
    cleaned = re.sub(r"\\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\\s+([.,;:])", r"\\1", cleaned)
    return cleaned.strip()


def strip_noise_lines(lines: Iterable[str]) -> list[str]:
    cleaned: list[str] = []
    pending_blank = False
    for raw in lines:
        raw_str = raw or ""
        core = raw_str.lstrip("-• ").strip()
        if not core:
            if cleaned and not pending_blank:
                cleaned.append("")
                pending_blank = True
            continue
        if contains_noise_phrase(core) or matches_noise_fragment(core):
            continue
        if contains_intake_form_language(core):
            continue
        low = core.lower()
        if "call" in low and "immediately" in low:
            continue
        if low.count(",") >= 4 and ("risk" in low or "hazard" in low):
            continue
        if sum(ch.isalpha() for ch in core) < 4:
            continue
        cleaned.append(raw_str.strip())
        pending_blank = False
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    return cleaned


def normalise_line_key(value: str) -> str:
    return _NON_ALNUM_RE.sub(" ", value.lower()).strip()


def clean_merge_fragment(value: str) -> str:
    if not value:
        return ""
    trimmed = strip_chunk_metadata(value).strip()
    if not trimmed:
        return ""
    if matches_noise_fragment(trimmed) or contains_noise_phrase(trimmed):
        return ""
    if is_admin_noise(trimmed):
        return ""
    if contains_intake_form_language(trimmed):
        return ""
    return trimmed


def limit_sentences(text: str, max_sentences: int) -> str:
    sentences = [segment.strip() for segment in SENTENCE_SPLIT_RE.split(text) if segment.strip()]
    if not sentences:
        return ""
    limited = " ".join(sentences[:max_sentences]).strip()
    return limited


def looks_all_caps(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    uppercase = sum(1 for ch in letters if ch.isupper())
    return uppercase / max(1, len(letters)) >= 0.8


def is_admin_noise(text: str) -> bool:
    low = text.lower()
    if any(keyword in low for keyword in _ADMIN_KEYWORDS):
        return True
    if low.startswith("document processed"):
        return True
    return False


def tokenize_for_similarity(text: str) -> Set[str]:
    return {token for token in _TOKEN_RE.findall(text.lower()) if len(token) >= 3}


def jaccard_similarity(tokens_a: Set[str], tokens_b: Set[str]) -> float:
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def strip_and_filter_sections(lines: Iterable[str]) -> List[str]:
    cleaned: List[str] = []
    for line in lines:
        sanitised = clean_merge_fragment(line)
        if sanitised:
            cleaned.append(sanitised)
    return cleaned

__all__ = [
    "SENTENCE_SPLIT_RE",
    "clean_text",
    "clean_merge_fragment",
    "contains_noise_phrase",
    "contains_intake_form_language",
    "is_admin_noise",
    "is_placeholder",
    "jaccard_similarity",
    "limit_sentences",
    "looks_all_caps",
    "matches_noise_fragment",
    "normalize_text",
    "normalise_line_key",
    "sanitise_keywords",
    "strip_chunk_metadata",
    "strip_noise_lines",
    "strip_and_filter_sections",
    "tokenize_for_similarity",
    "_VITAL_TOKENS",
    "_DETAIL_VITAL_RE",
]
