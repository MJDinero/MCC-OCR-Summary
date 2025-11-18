"""Utility helpers for cleaning summariser payloads and extracting context."""

from __future__ import annotations

import re
from typing import List, Sequence, Tuple

_EXCESS_WS_RE = re.compile(r"\s+")
_REASON_PREFIX_RE = re.compile(
    r"(?i)^\s*(?:reason(?:s)?\s+for\s+visit|chief\s+complaint)\s*[:\-]?\s*"
)
_CONSENT_LINE_RE = re.compile(
    r"(?i)^(?:i\s+(?:understand|authorize|consent)|to\s+treat\s+my\s+condition)\b"
)
_WARNING_PHRASES = (
    "this is especially important if you are taking diabetes medicines or blood thinners",
    "i understand that the following treatment",
    "i understand that the following procedure",
    "i understand that there are risks",
    "i consent to the procedure",
)

_SIGNATURE_CUES = (
    "respectfully",
    "sincerely",
    "regards",
    "thank you",
    "signed electronically",
    "electronically signed",
    "dictated by",
    "attending physician",
    "attending provider",
    "provider signature",
    "provider:",
)
_SIGNATURE_NAME_RE = re.compile(
    r"(?:(?P<prefix>dr\.?|doctor)\s+)?"
    r"(?P<name>[A-Z][A-Za-z\.'-]+(?:\s+(?:[A-Z][A-Za-z\.'-]+|[A-Z]\.))+)"
    r"(?:,\s*(?P<suffix>(?:M\.?D\.?|D\.?O\.?|DO|PA-C|NP|ARNP|FNP|DNP|DC|DPM)))?",
    re.IGNORECASE,
)
_SUFFIX_NORMALISATION = {
    "md": "M.D.",
    "m.d.": "M.D.",
    "do": "D.O.",
    "d.o.": "D.O.",
    "pa-c": "PA-C",
    "np": "NP",
    "arnp": "ARNP",
    "fnp": "FNP",
    "dnp": "DNP",
    "dc": "DC",
    "dpm": "DPM",
}

_DIAG_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\blumbar\s+(?:disc|discopathy|radiculopathy)\b", re.IGNORECASE), "Lumbar discopathy"),
    (re.compile(r"\bcervical\s+(?:disc|discopathy|radiculopathy)\b", re.IGNORECASE), "Cervical discopathy"),
    (re.compile(r"\bthoracic\s+(?:disc|discopathy|radiculopathy)\b", re.IGNORECASE), "Thoracic discopathy"),
    (re.compile(r"\bneck\s+pain\b", re.IGNORECASE), "Neck pain"),
    (re.compile(r"\bback\s+pain\b", re.IGNORECASE), "Back pain"),
    (re.compile(r"\bjoint\s+pain\b", re.IGNORECASE), "Joint pain"),
)

_VITAL_PATTERNS = {
    "blood_pressure": re.compile(r"(?:blood\s+pressure|bp)\s*[:\-=]?\s*(\d{2,3}/\d{2,3})", re.IGNORECASE),
    "heart_rate": re.compile(r"(?:heart\s+rate|pulse|hr)\s*[:\-=]?\s*(\d{2,3})", re.IGNORECASE),
    "resp_rate": re.compile(r"(?:resp(?:iratory)?\s+rate|rr)\s*[:\-=]?\s*(\d{1,2})", re.IGNORECASE),
    "temperature": re.compile(
        r"(?:temp(?:erature)?|t)\s*[:\-=]?\s*(\d{2,3}(?:\.\d+)?)\s*([CF]|°F|°C)?",
        re.IGNORECASE,
    ),
    "spo2": re.compile(r"(?:spo2|o2\s*(?:sat|saturation))\s*[:\-=]?\s*(\d{2,3})%?", re.IGNORECASE),
}
_VITAL_TOKENS = (
    "bp",
    "blood pressure",
    "pulse",
    "heart rate",
    "hr",
    "temp",
    "temperature",
    "rr",
    "resp",
    "spo2",
    "o2",
    "oxygen",
    "sat",
)


def _normalise_whitespace(value: str) -> str:
    return _EXCESS_WS_RE.sub(" ", value or "").strip()


def prune_admin_text(value: str) -> str:
    """Remove consent/disclaimer prefixes but keep meaningful content."""

    if not value:
        return ""
    text = _normalise_whitespace(value)
    if not text:
        return ""
    low = text.lower()
    if _CONSENT_LINE_RE.match(text):
        return ""
    if any(fragment in low for fragment in _WARNING_PHRASES):
        return ""
    text = _REASON_PREFIX_RE.sub("", text, count=1)
    text = text.strip(":- \u2014")
    if not text:
        return ""
    return text


def select_reason_statements(
    overview_lines: Sequence[str], key_points: Sequence[str], *, limit: int
) -> List[str]:
    """Prioritise reason-for-visit sentences from overview/key-point arrays."""

    ordered_sources: tuple[Sequence[str], ...] = (overview_lines, key_points)
    seen: set[str] = set()
    results: List[str] = []
    for source in ordered_sources:
        for line in source:
            cleaned = prune_admin_text(line)
            if not cleaned:
                continue
            norm = cleaned.lower()
            if norm in seen:
                continue
            seen.add(norm)
            results.append(cleaned)
            if len(results) >= limit:
                return results
    return results


def select_plan_statements(lines: Sequence[str], *, limit: int) -> List[str]:
    """Clean plan/care statements and cap at requested limit."""

    results: List[str] = []
    seen: set[str] = set()
    for line in lines:
        cleaned = prune_admin_text(line)
        if not cleaned:
            continue
        norm = cleaned.lower()
        if norm in seen:
            continue
        seen.add(norm)
        results.append(cleaned)
        if len(results) >= limit:
            break
    return results


def prepare_clinical_findings(
    lines: Sequence[str],
    *,
    limit: int,
    vitals_summary: str | None = None,
) -> List[str]:
    """Condense exam findings while ensuring vitals are surfaced first."""

    results: List[str] = []
    seen: set[str] = set()
    if vitals_summary:
        cleaned_vitals = prune_admin_text(vitals_summary)
        if cleaned_vitals:
            results.append(cleaned_vitals)
            seen.add(cleaned_vitals.lower())
    for line in lines:
        cleaned = prune_admin_text(line)
        if not cleaned:
            continue
        norm = cleaned.lower()
        if norm in seen:
            continue
        seen.add(norm)
        results.append(cleaned)
        if len(results) >= limit:
            break
    return results


def summarize_vitals(text: str) -> str | None:
    """Convert scattered vitals into a concise narrative sentence."""

    if not text:
        return None
    matches: dict[str, str] = {}
    temperature_unit = ""
    for key, pattern in _VITAL_PATTERNS.items():
        match = pattern.search(text)
        if not match:
            continue
        if key == "temperature":
            value, unit = match.groups()
            matches[key] = value
            temperature_unit = unit or ""
        else:
            matches[key] = match.group(1)
    if not matches:
        return None
    fragments: List[str] = []
    bp_val = matches.get("blood_pressure")
    if bp_val:
        fragments.append(f"BP {bp_val} mmHg")
    hr_val = matches.get("heart_rate")
    if hr_val:
        fragments.append(f"HR {hr_val} bpm")
    rr_val = matches.get("resp_rate")
    if rr_val:
        fragments.append(f"RR {rr_val}/min")
    temp_val = matches.get("temperature")
    if temp_val:
        unit = temperature_unit.upper().replace("°", "") if temperature_unit else "F"
        fragments.append(f"Temp {temp_val}°{unit}")
    spo2_val = matches.get("spo2")
    if spo2_val:
        fragments.append(f"SpO2 {spo2_val}%")
    if not fragments:
        return None
    return "Vitals: " + ", ".join(fragments)


def looks_like_vitals_table(line: str) -> bool:
    """Detect multi-column vitals rows to avoid dumping raw tables."""

    if not line:
        return False
    low = line.lower()
    token_hits = sum(1 for token in _VITAL_TOKENS if token in low)
    digit_count = sum(1 for ch in line if ch.isdigit())
    return token_hits >= 2 and digit_count >= 4


def extract_signature_providers(text: str) -> Tuple[str | None, List[str]]:
    """Parse provider names from signature blocks."""

    if not text:
        return None, []
    names: List[str] = []
    seen: set[str] = set()
    primary: str | None = None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for idx, line in enumerate(lines):
        low = line.lower()
        search_line = line
        if any(cue in low for cue in _SIGNATURE_CUES) and idx + 1 < len(lines):
            search_line = f"{line} {lines[idx + 1]}"
        elif idx + 1 < len(lines) and lines[idx + 1].lower() in _SIGNATURE_CUES:
            search_line = f"{line} {lines[idx + 1]}"
        for match in _SIGNATURE_NAME_RE.finditer(search_line):
            prefix = match.group("prefix") or ""
            raw_name = match.group("name")
            suffix = match.group("suffix") or ""
            if not prefix and not suffix:
                continue
            formatted = _format_provider_name(prefix, raw_name, suffix)
            if not formatted or formatted.lower() in seen:
                continue
            seen.add(formatted.lower())
            names.append(formatted)
            if primary is None:
                primary = formatted
    return primary, names


def _format_provider_name(prefix: str, raw_name: str, suffix: str) -> str:
    base = _normalise_whitespace(raw_name).rstrip(",")
    pieces: List[str] = []
    prefix_clean = prefix.lower() if prefix else ""
    if prefix_clean:
        if prefix_clean.startswith("dr"):
            pieces.append("Dr.")
        else:
            pieces.append(prefix.title())
    pieces.append(base)
    suffix_clean = suffix.strip().lower()
    suffix_norm = _SUFFIX_NORMALISATION.get(suffix_clean, suffix.upper())
    if (
        not prefix_clean
        and suffix_norm in {"M.D.", "D.O."}
        and not base.lower().startswith("dr ")
    ):
        pieces.insert(0, "Dr.")
    if suffix_norm:
        return f"{' '.join(pieces)}, {suffix_norm}"
    return " ".join(pieces)


def extract_additional_diagnoses(text: str) -> List[str]:
    """Derive diagnoses for common spine/pain complaints from OCR text."""

    if not text:
        return []
    matches: List[str] = []
    seen: set[str] = set()
    for pattern, label in _DIAG_PATTERNS:
        if pattern.search(text):
            norm = label.lower()
            if norm in seen:
                continue
            seen.add(norm)
            matches.append(label)
    return matches
