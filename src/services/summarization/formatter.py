"""Formatter helpers for MCC Bible compliant summaries."""

from __future__ import annotations

from typing import Sequence


def _format_bullets(lines: Sequence[str], *, fallback: str) -> str:
    entries = [line.strip() for line in lines if line.strip()]
    if not entries:
        return f"- {fallback}"
    return "\n".join(f"- {entry}" for entry in entries)


def build_mcc_bible_summary(
    *,
    chunk_count: int,
    facility: str | None,
    provider_seen: str | None,
    reason_lines: Sequence[str],
    clinical_findings: Sequence[str],
    care_plan: Sequence[str],
    diagnoses: Sequence[str],
    healthcare_providers: Sequence[str],
    medications: Sequence[str],
) -> str:
    provider_lines = []
    if provider_seen:
        provider_lines.append(provider_seen)
    else:
        provider_lines.append("Provider not documented.")
    if facility:
        provider_lines.append(f"Facility: {facility}")
    provider_lines.append(f"Document processed in {chunk_count} chunk(s).")

    sections = [
        ("Provider Seen", "\n".join(line for line in provider_lines if line).strip()),
        (
            "Reason for Visit",
            _format_bullets(reason_lines, fallback="Reason not documented."),
        ),
        (
            "Clinical Findings",
            _format_bullets(
                clinical_findings,
                fallback="No specific findings documented in OCR text.",
            ),
        ),
        (
            "Treatment / Follow-up Plan",
            _format_bullets(
                care_plan, fallback="No active treatment or follow-up plan recorded."
            ),
        ),
        (
            "Diagnoses",
            _format_bullets(diagnoses, fallback="No diagnoses documented."),
        ),
        (
            "Healthcare Providers",
            _format_bullets(healthcare_providers, fallback="Not listed."),
        ),
        (
            "Medications / Prescriptions",
            _format_bullets(
                medications, fallback="No medications or prescriptions recorded."
            ),
        ),
    ]
    return "\n\n".join(
        f"{heading}:\n{body.strip()}"
        for heading, body in sections
        if body.strip()
    )
