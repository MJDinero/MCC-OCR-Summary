"""# MCC Medical Summary Generator (Bible-Compliant)

Enhanced summarisation module producing a structured Medical Summary aligned
with the MCC Training Bible. It extracts the following narrative sections:
    - Provider Seen
    - Reason for Visit
    - Clinical Findings
    - Treatment / Follow-Up Plan

And aggregates three index style lists:
    - Diagnoses (ICD-10 where present)
    - Providers
    - Medications / Prescriptions

Returned structure remains backwards-compatible with the PDF writer which
expects a dictionary containing the legacy display headings. The composed,
multi-section medical narrative (including indices) is placed in the
"Medical Summary" field; other legacy fields are preserved (or set to 'N/A')
to avoid downstream breakage.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import List, Protocol, Dict, Any, Optional
import logging
import math
import time
import random
import socket

from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

from src.errors import SummarizationError
import re

_LOG = logging.getLogger("summariser")

try:  # pragma: no cover - optional metrics
    from prometheus_client import Counter, Histogram  # type: ignore
    _SUM_CALLS = Counter("summariser_calls_total", "Total summariser calls", ["status"])
    _SUM_LATENCY = Histogram("summariser_latency_seconds", "Summariser latency seconds")
except Exception:  # pragma: no cover
    _SUM_CALLS = None  # type: ignore
    _SUM_LATENCY = None  # type: ignore


class TransientSummarizationError(Exception):
    """Internal marker for transient backend errors to trigger retry."""


class SummarizationBackend(Protocol):  # pragma: no cover - interface only
    def summarise(self, text: str) -> Dict[str, str]:  # noqa: D401
        ...


class OpenAIBackend:  # pragma: no cover - network heavy, exercised in integration environment
    """Concrete backend using OpenAI Responses API (ChatGPT class models).

    For each chunk we request STRICT JSON with keys:
        provider_seen, reason_for_visit, clinical_findings, treatment_plan,
        diagnoses, providers, medications

    Where list-like values (diagnoses/providers/medications) may be either a
    single string or a JSON array of strings; we normalise downstream.
    """

    SYSTEM_PROMPT = (
        "You are a professional medical summarization assistant. "
        "Extract and summarise the medical content into the following JSON structure with EXACT keys: "
        "provider_seen, reason_for_visit, clinical_findings, treatment_plan, diagnoses, providers, medications. "
        "Rules: factual, concise, no speculative language, no markdown, no asterisks. "
        "diagnoses should include ICD-10 codes when explicitly present; otherwise raw description. "
        "providers list unique provider names or roles mentioned. medications list drug names or prescribed items. "
        "Return ONLY JSON."
    )

    def __init__(self, model: str = "gpt-4o-mini", api_key: str | None = None):
        self.model = model
        self.api_key = api_key

    def _invoke_with_retry(self, prompt: str, meta: Dict[str, Any]) -> Dict[str, str]:  # pragma: no cover - network
        """Call OpenAI with retry + structured logging returning chunk JSON fields."""
        try:
            from openai import OpenAI  # type: ignore
            import openai as _openai_mod  # type: ignore
            # DNS pre-resolution (each invocation path) for diagnostic correlation
            try:
                resolved_ip = socket.gethostbyname("api.openai.com")
                _LOG.debug("openai_dns_resolution", extra={"ip": resolved_ip})
            except Exception as de:  # pragma: no cover
                _LOG.warning("openai_dns_resolution_failed", extra={"error": str(de)})
            client = OpenAI(api_key=self.api_key, timeout=180)  # increased timeout
            if not hasattr(self, "_logged_version"):
                _LOG.info("openai_sdk_version", extra={"version": getattr(_openai_mod, '__version__', 'unknown')})
                self._logged_version = True  # type: ignore
            # Import exception classes for granular handling
            API_CONN_ERR = getattr(_openai_mod, 'APIConnectionError', tuple())
            API_TIMEOUT_ERR = getattr(_openai_mod, 'APITimeoutError', tuple())
            API_ERROR = getattr(_openai_mod, 'APIError', tuple())
            RATE_LIMIT_ERR = getattr(_openai_mod, 'RateLimitError', tuple())
        except Exception as exc:  # pragma: no cover - import/runtime
            raise TransientSummarizationError(f"OpenAI import/init failed: {exc}") from exc

        max_attempts = 6  # increased attempts for transient network instability
        base_delay = 1.0
        last_exc: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            start = time.perf_counter()
            _LOG.info(
                "summariser_call_start",
                extra={"attempt": attempt, "model": self.model, **meta},
            )
            try:
                response = client.responses.create(
                    model=self.model,
                    input=prompt,
                    temperature=0,
                )
                latency_ms = round((time.perf_counter() - start) * 1000, 2)
                summary_text = getattr(response, 'output_text', '') or ''
                import json
                try:
                    data = json.loads(summary_text)
                except Exception:  # salvage JSON
                    import re as _re
                    m = _re.search(r"{.*}", summary_text, _re.DOTALL)
                    data = json.loads(m.group(0)) if m else {}
                required_chunk_keys = [
                    'provider_seen', 'reason_for_visit', 'clinical_findings', 'treatment_plan',
                    'diagnoses', 'providers', 'medications'
                ]
                out_snake: Dict[str, Any] = {}
                for k in required_chunk_keys:
                    v = data.get(k) if isinstance(data, dict) else None
                    out_snake[k] = v if v not in (None, '') else ''
                _LOG.info(
                    "summariser_call_complete",
                    extra={"attempt": attempt, "latency_ms": latency_ms, **meta},
                )
                return out_snake
            except (API_CONN_ERR, API_TIMEOUT_ERR) as exc:  # type: ignore
                last_exc = exc  # retriable
                wait = min(2 ** (attempt - 1) + random.random(), 30.0)
                _LOG.warning(
                    "summariser_retry_attempt",
                    extra={
                        "attempt": attempt,
                        "error": str(exc),
                        "error_type": exc.__class__.__name__,
                        "wait_seconds": round(wait, 2),
                        "category": "connection",
                        **meta,
                    },
                )
                time.sleep(wait)
                continue
            except (RATE_LIMIT_ERR,) as exc:  # type: ignore
                last_exc = exc
                wait = min(base_delay * attempt + random.random(), 20.0)
                _LOG.warning(
                    "summariser_retry_attempt",
                    extra={
                        "attempt": attempt,
                        "error": str(exc),
                        "error_type": exc.__class__.__name__,
                        "wait_seconds": round(wait, 2),
                        "category": "rate_limit",
                        **meta,
                    },
                )
                time.sleep(wait)
                continue
            except API_ERROR as exc:  # type: ignore
                # Distinguish authentication/authorization issues vs transient 5xx
                code = getattr(exc, 'status_code', None)
                retriable = code and 500 <= int(code) < 600
                if code in (401, 403):
                    _LOG.error(
                        "summariser_auth_failure",
                        extra={
                            "attempt": attempt,
                            "status_code": code,
                            "error": str(exc),
                            "hint": "Check OPENAI_API_KEY validity and secret injection",
                            **meta,
                        },
                    )
                    # Authentication errors are not retried – escalate immediately
                    raise SummarizationError(f"OpenAI authentication failed (status {code})") from exc
                _LOG.error(
                    "summariser_call_failed",
                    extra={
                        "attempt": attempt,
                        "error": str(exc),
                        "error_type": exc.__class__.__name__,
                        "retriable": retriable,
                        "status_code": code,
                        **meta,
                    },
                )
                if retriable and attempt < max_attempts:
                    wait = min(2 ** (attempt - 1) + random.random(), 30.0)
                    time.sleep(wait)
                    last_exc = exc
                    continue
                raise TransientSummarizationError(str(exc)) from exc
            except Exception as exc:  # unexpected
                _LOG.error(
                    "summariser_call_failed",
                    extra={
                        "attempt": attempt,
                        "error": str(exc),
                        "error_type": exc.__class__.__name__,
                        "retriable": False,
                        "category": "unexpected",
                        **meta,
                    },
                )
                raise TransientSummarizationError(str(exc)) from exc
        # Exhausted
        raise TransientSummarizationError(f"Retries exhausted: {last_exc}")

    def summarise(self, text: str) -> Dict[str, Any]:  # pragma: no cover - network
        # Lightweight offline/mock mode: if api_key indicates mock usage, return deterministic canned structure.
        if self.api_key and isinstance(self.api_key, str) and self.api_key.lower().startswith("mock"):
            _LOG.info("summariser_mock_backend", extra={"chars": len(text), "model": self.model})
            snippet = text.strip().split('\n')[0][:120]
            return {
                'provider_seen': 'Unknown Provider',
                'reason_for_visit': snippet or 'N/A',
                'clinical_findings': 'No clinical findings extracted (mock mode).',
                'treatment_plan': 'No treatment plan extracted (mock mode).',
                'diagnoses': [],
                'providers': [],
                'medications': [],
            }
        char_count = len(text)
        approx_tokens = math.ceil(char_count / 4)
        meta = {"char_count": char_count, "approx_tokens": approx_tokens, "model": self.model}
        _LOG.info("summariser_prompt_meta", extra=meta)
        prompt = f"SYSTEM:\n{self.SYSTEM_PROMPT}\n---\nOCR_TEXT:\n{text[:100000]}"
        return self._invoke_with_retry(prompt, meta)


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitize_text(raw: str, max_chars: int) -> str:
    """Remove control chars and truncate hard at max_chars * 6 (pre-chunk safety)."""
    cleaned = _CONTROL_CHARS_RE.sub("", raw)
    limit = max_chars * 6
    if len(cleaned) > limit:
        cleaned = cleaned[:limit]
    return cleaned


def _default_chunker(text: str, max_chars: int) -> List[str]:
    """Greedy word-boundary chunker.

    Tries to keep chunk count low (<= ~50) while respecting max_chars. For
    moderately small texts (like tests) this will usually produce 1-2 chunks.
    """
    if len(text) <= max_chars:
        return [text]
    words = text.split()
    chunks: List[str] = []
    current: List[str] = []
    length = 0
    for w in words:
        add_len = len(w) + (1 if current else 0)
        if length + add_len > max_chars and current:
            chunks.append(" ".join(current))
            current = [w]
            length = len(w)
        else:
            current.append(w)
            length += add_len
    if current:
        chunks.append(" ".join(current))
    return chunks


class SummarizerChunker:
    """Utility to split large OCR text into deterministic ~2.5K char segments on whitespace boundaries."""

    def __init__(self, target_size: int = 2500, hard_max: int = 3000):
        self.target = target_size
        self.hard_max = hard_max

    def split(self, text: str) -> List[str]:
        if len(text) <= self.hard_max:
            return [text]
        words = text.split()
        chunks: List[str] = []
        current: List[str] = []
        size = 0
        for w in words:
            add = len(w) + (1 if current else 0)
            if size + add > self.target and current:
                chunks.append(" ".join(current))
                current = [w]
                size = len(w)
            elif size + add > self.hard_max:  # emergency hard cap
                chunks.append(" ".join(current + [w]))
                current = []
                size = 0
            else:
                current.append(w)
                size += add
        if current:
            chunks.append(" ".join(current))
        return chunks


@dataclass
class Summariser:
    backend: SummarizationBackend
    chunk_target_chars: int = 2500
    chunk_hard_max: int = 3000
    multi_chunk_threshold: int = 3000  # any text longer than this will trigger chunking

    @retry(
        wait=wait_exponential(multiplier=0.5, max=6),
        stop=stop_after_attempt(4),
        retry=retry_if_exception_type(TransientSummarizationError),
        reraise=True,
    )
    def _summarise_chunk(self, chunk: str) -> Dict[str, Any]:
        return self.backend.summarise(chunk)

    def summarise(self, text: str) -> Dict[str, str]:
        # Defensive guard: handle non-string inputs gracefully (should be str under normal API contract)
        if text is None:
            raise SummarizationError("Input text empty")
        if not isinstance(text, str):
            text = str(text)
        if not text.strip():
            raise SummarizationError("Input text empty")
        try:
            sanitized = _sanitize_text(text, self.chunk_hard_max)
            chunker = SummarizerChunker(self.chunk_target_chars, self.chunk_hard_max)
            chunks = chunker.split(sanitized)
            multi = len(chunks) > 1
            _LOG.info("summariser_chunking", extra={"chunks": len(chunks), "multi": multi})

            collected: List[Dict[str, Any]] = []
            for idx, ch in enumerate(chunks, start=1):
                _LOG.info("summariser_chunk_start", extra={"index": idx, "size": len(ch)})
                part = self._summarise_chunk(ch)
                # Normalise unexpected value types (e.g. dict) to safe string forms before merge logic.
                # Keep list fields intact for list-merge handling.
                list_fields = {"diagnoses", "providers", "medications"}
                for k, v in list(part.items()):
                    if k in list_fields:
                        # ensure list fields are either list[str] or left as-is if string; convert singletons
                        if isinstance(v, (set, tuple)):
                            part[k] = list(v)
                        elif v is None:
                            part[k] = []
                        # if dict slipped in, flatten dict values
                        elif isinstance(v, dict):
                            flat = []
                            for subk, subv in v.items():
                                subv_s = str(subv).strip() if subv is not None else ''
                                if subv_s:
                                    flat.append(subv_s)
                            part[k] = flat
                        elif not isinstance(v, list) and not isinstance(v, str):
                            part[k] = [str(v)]
                    else:
                        if isinstance(v, dict):
                            # Flatten dict into "key: value; ..." deterministic ordering by insertion
                            comp = "; ".join(
                                f"{dk}: {str(dv).strip()}" for dk, dv in v.items() if str(dv).strip()
                            )
                            part[k] = comp
                            _LOG.info(
                                "summariser_value_coerced",
                                extra={"key": k, "original_type": "dict", "length": len(comp)},
                            )
                        elif isinstance(v, (list, tuple, set)):
                            joined = ", ".join(str(x).strip() for x in v if str(x).strip())
                            part[k] = joined
                            _LOG.info(
                                "summariser_value_coerced",
                                extra={"key": k, "original_type": type(v).__name__, "length": len(joined)},
                            )
                        elif v is None:
                            part[k] = ""
                if idx == 1:
                    type_map = {k: type(v).__name__ for k, v in part.items()}
                    _LOG.info("summariser_chunk_types", extra={"index": idx, "types": type_map})
                collected.append(part)
                _LOG.info("summariser_chunk_complete", extra={"index": idx})

            # Legacy single-structure backend (tests) compatibility: if first chunk already
            # returns the legacy snake_case keys, bypass new merging logic.
            legacy_keys = {"patient_info", "medical_summary", "billing_highlights", "legal_notes"}
            new_schema_keys = {"provider_seen", "reason_for_visit", "clinical_findings", "treatment_plan"}
            first_keys = set(collected[0].keys()) if collected else set()
            if collected and (first_keys & legacy_keys) and not (first_keys & new_schema_keys):
                # Treat as legacy backend output (possibly partial)
                src = collected[0]
                mapping = {
                    'patient_info': 'Patient Information',
                    'medical_summary': 'Medical Summary',
                    'billing_highlights': 'Billing Highlights',
                    'legal_notes': 'Legal / Notes',
                }
                display_legacy: Dict[str, str] = {}
                def _safe_strip(val: Any) -> str:
                    if isinstance(val, str):
                        return val.strip()
                    try:
                        return str(val).strip()
                    except Exception:
                        return ''
                for sk, heading in mapping.items():
                    raw_val = src.get(sk)
                    # treat None explicitly as missing
                    if raw_val is None:
                        display_legacy[heading] = 'N/A'
                        continue
                    coerced = _safe_strip(raw_val)
                    display_legacy[heading] = coerced if coerced else 'N/A'
                return display_legacy

            # Merge strategy
            def _merge_field(key: str) -> str:
                vals = [c.get(key, '') for c in collected if c.get(key) is not None]
                if not vals:
                    return ''
                seen: set[str] = set()
                ordered: List[str] = []
                for v in vals:
                    try:
                        logging.debug(f"Normalizing field={key!r} value_type={type(v).__name__}")
                        if isinstance(v, (dict, list)):
                            v = json.dumps(v, ensure_ascii=False)
                        elif isinstance(v, (int, float)):
                            v = str(v)
                        if not isinstance(v, str):
                            v = str(v)
                        v_norm = v.strip()
                    except Exception:
                        continue
                    if v_norm and v_norm not in seen:
                        seen.add(v_norm)
                        ordered.append(v_norm)
                return '\n'.join(ordered) if ordered else 'N/A'

            def _merge_list_field(key: str) -> List[str]:
                items: List[str] = []
                for c in collected:
                    raw = c.get(key)
                    if not raw:
                        continue
                    if isinstance(raw, list):
                        for x in raw:
                            xs = str(x).strip()
                            if xs:
                                items.append(xs)
                    elif isinstance(raw, (set, tuple)):
                        for x in raw:
                            xs = str(x).strip()
                            if xs:
                                items.append(xs)
                    elif isinstance(raw, dict):
                        # Append dict values (flatten) – keys not semantically needed here
                        for dv in raw.values():
                            dvs = str(dv).strip()
                            if dvs:
                                items.append(dvs)
                    elif isinstance(raw, str) and raw.strip():
                        parts = [p.strip() for p in raw.split(',') if p.strip()]
                        items.extend(parts if len(parts) > 1 else [raw.strip()])
                    else:
                        # Fallback: coerce to string
                        coerced = str(raw).strip()
                        if coerced:
                            items.append(coerced)
                seen: set[str] = set()
                deduped: List[str] = []
                for it in items:
                    if it not in seen:
                        seen.add(it)
                        deduped.append(it)
                return deduped

            provider_seen = _merge_field('provider_seen')
            reason_for_visit = _merge_field('reason_for_visit')
            clinical_findings = _merge_field('clinical_findings')
            treatment_plan = _merge_field('treatment_plan')
            diagnoses_list = _merge_list_field('diagnoses')
            providers_list = _merge_list_field('providers')
            meds_list = _merge_list_field('medications')

            # Compose final medical summary narrative (plain text, headers bold style not applied here; PDF layer can style)
            def _fmt_section(title: str, body: str) -> str:
                body_eff = body.strip() if body.strip() else 'N/A'
                return f"{title}:\n{body_eff}\n"

            narrative_parts = [
                _fmt_section('Provider Seen', provider_seen),
                _fmt_section('Reason for Visit', reason_for_visit),
                _fmt_section('Clinical Findings', clinical_findings),
                _fmt_section('Treatment / Follow-Up Plan', treatment_plan),
            ]

            def _fmt_list(title: str, items: List[str]) -> str:
                if not items:
                    return f"{title}:\nN/A\n"
                return f"{title}:\n" + '\n'.join(f"- {i}" for i in items) + '\n'

            index_sections = [
                _fmt_list('Diagnoses', diagnoses_list),
                _fmt_list('Providers', providers_list),
                _fmt_list('Medications / Prescriptions', meds_list),
            ]

            full_medical_summary = '\n'.join(narrative_parts + index_sections).strip()
            avg_chunk = round(sum(len(c) for c in chunks) / len(chunks), 2)
            _LOG.info(
                "summariser_generation_complete",
                extra={
                    "chunks": len(chunks),
                    "avg_chunk_chars": avg_chunk,
                    "diagnoses": len(diagnoses_list),
                    "providers": len(providers_list),
                    "medications": len(meds_list),
                },
            )
            _LOG.info(
                "summariser_merge_complete",
                extra={
                    "event": "chunk_merge_complete",
                    "emoji": "📄",
                    "chunk_count": len(chunks),
                    "avg_chunk_chars": avg_chunk,
                    "list_sections": {
                        "diagnoses": len(diagnoses_list),
                        "providers": len(providers_list),
                        "medications": len(meds_list),
                    },
                },
            )
            # Adapt to legacy output contract expected by PDF writer
            display: Dict[str, str] = {
                'Patient Information': 'N/A',
                'Medical Summary': full_medical_summary,
                'Billing Highlights': 'N/A',
                'Legal / Notes': 'N/A',
            }
            # Provide structured lists on the returned dict under side-channel keys for enhanced PDF writer
            display["_diagnoses_list"] = "\n".join(diagnoses_list)
            display["_providers_list"] = "\n".join(providers_list)
            display["_medications_list"] = "\n".join(meds_list)
            return display
        except TransientSummarizationError as exc:
            raise SummarizationError(f"Transient summarisation failed after retries: {exc}") from exc
        except SummarizationError:
            raise
        except Exception as exc:
            raise SummarizationError(f"Unexpected summarisation error: {exc}") from exc


class StructuredSummariser(Summariser):
    """Explicit alias for the Bible-compliant structured summariser variant.

    Provided to allow unambiguous selection in application wiring / dependency
    injection without changing existing test references to `Summariser`.
    """
    def summarise_text(self, text: str) -> Dict[str, str]:
        """Convenience wrapper kept for backwards compatibility with earlier smoke scripts."""
        _LOG.info("summariser_text_wrapper", extra={"event": "summarise_text_called", "len": len(text) if isinstance(text, str) else None})
        result = self.summarise(text)
        _LOG.info("summariser_text_wrapper_complete", extra={"event": "summarise_text_done", "keys": list(result.keys())})
        return result
    pass


__all__ = ["Summariser", "StructuredSummariser", "SummarizationBackend", "OpenAIBackend"]
