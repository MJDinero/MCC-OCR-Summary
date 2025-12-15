#!/usr/bin/env python3
"""Lightweight PDF validator for MCC summaries.

The script verifies that the rendered PDF has the expected number of pages and
that every MCC heading is present at least once in the extracted text. It is
intended to be used as a fast preflight guard before running larger pipelines.
"""

from __future__ import annotations

import argparse
import sys
import json
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Iterable, Sequence, Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from pypdf import PdfReader  # type: ignore
except Exception as exc:  # pragma: no cover - import guard for helpful error
    raise SystemExit(f"Failed to import pypdf: {exc}") from exc

from src.models.summary_contract import SummaryContract


DEFAULT_REQUIRED_HEADINGS = [
    "Provider Seen",
    "Reason for Visit",
    "Clinical Findings",
    "Treatment / Follow-up Plan",
    "Diagnoses",
    "Healthcare Providers",
    "Medications / Prescriptions",
]


class ValidationError(RuntimeError):
    """Raised when the validator detects a mismatch."""


@dataclass
class ValidationResult:
    pdf_path: Path
    page_count: int
    expected_pages: int
    missing_headings: Sequence[str]

    @property
    def is_success(self) -> bool:
        return (
            self.page_count == self.expected_pages
            and not self.missing_headings
        )


def _extract_pdf_text(pdf_path: Path) -> tuple[int, list[str]]:
    """Read the PDF and return page count plus each page's text."""
    reader = PdfReader(str(pdf_path))
    texts: list[str] = []
    for idx, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        normalised = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        texts.append(normalised)
        if not normalised:
            print(
                f"[validator] warning: no text extracted for page {idx}",
                file=sys.stderr,
            )
    return len(reader.pages), texts


def _validate_headings(
    page_texts: Iterable[str], required_headings: Sequence[str]
) -> list[str]:
    combined = "\n".join(page_texts).lower()
    missing = [
        heading
        for heading in required_headings
        if heading.lower() not in combined
    ]
    return missing


def validate_pdf(
    *,
    pdf_path: Path,
    expected_pages: int,
    required_headings: Sequence[str],
) -> ValidationResult:
    page_count, page_texts = _extract_pdf_text(pdf_path)
    missing = _validate_headings(page_texts, required_headings)
    return ValidationResult(
        pdf_path=pdf_path,
        page_count=page_count,
        expected_pages=expected_pages,
        missing_headings=missing,
    )


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate rendered MCC summary PDFs for structural correctness."
        )
    )
    parser.add_argument(
        "--pdf-path",
        required=True,
        type=Path,
        help="Path to the PDF output to inspect.",
    )
    parser.add_argument(
        "--expected-pages",
        required=True,
        type=int,
        help="Expected number of pages in the PDF.",
    )
    parser.add_argument(
        "--required-heading",
        action="append",
        dest="required_headings",
        default=None,
        help=(
            "Required heading to verify in the PDF text. "
            "Specify multiple times to override defaults."
        ),
    )
    parser.add_argument(
        "--summary-json",
        type=Path,
        help="Optional path to the structured summary JSON payload for evidence validation.",
    )
    parser.add_argument(
        "--strict-evidence",
        action="store_true",
        help="Fail when structured claims/evidence are missing.",
    )
    return parser.parse_args(argv)


def _load_summary_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValidationError(f"Summary JSON not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Invalid summary JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValidationError("Summary JSON must contain an object.")
    return data


def _validate_claims(summary: Mapping[str, Any], *, strict: bool) -> bool:
    claims = summary.get("_claims")
    if claims is None or not isinstance(claims, list) or not claims:
        if strict:
            raise ValidationError("Summary JSON is missing '_claims'.")
        return False
    evidence_list = summary.get("_evidence_spans") or []
    evidence_lookup: dict[str, Mapping[str, Any]] = {}
    if isinstance(evidence_list, list):
        for span in evidence_list:
            if isinstance(span, Mapping) and "span_id" in span:
                evidence_lookup[str(span["span_id"])] = span
    sections_seen: dict[str, set[str]] = {}
    for claim in claims:
        if not isinstance(claim, Mapping):
            raise ValidationError("Claim entries must be objects.")
        section = str(claim.get("section") or "Unknown")
        status = str(claim.get("status") or "").lower()
        sections_seen.setdefault(section, set()).add(status)
        if status == "supported":
            refs = claim.get("evidence_refs") or []
            if not refs:
                raise ValidationError(f"Claim {claim.get('claim_id')} missing evidence references.")
            for ref in refs:
                if ref not in evidence_lookup:
                    raise ValidationError(
                        f"Claim {claim.get('claim_id')} references unknown evidence span {ref}."
                    )
        elif status == "illegible":
            if claim.get("value") != "Illegible/Unknown":
                raise ValidationError(
                    f"Illegible claim {claim.get('claim_id')} must use 'Illegible/Unknown' text."
                )
    missing_supported = [
        section
        for section, statuses in sections_seen.items()
        if "supported" not in statuses and "illegible" not in statuses
    ]
    if missing_supported:
        raise ValidationError(
            f"Sections lack supported or illegible claims: {', '.join(sorted(missing_supported))}"
        )
    return True


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    pdf_path = args.pdf_path
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    headings = (
        args.required_headings if args.required_headings else DEFAULT_REQUIRED_HEADINGS
    )

    result = validate_pdf(
        pdf_path=pdf_path,
        expected_pages=args.expected_pages,
        required_headings=headings,
    )

    if not result.is_success:
        problems: list[str] = []
        if result.page_count != result.expected_pages:
            problems.append(
                f"expected {result.expected_pages} pages "
                f"but found {result.page_count}"
            )
        if result.missing_headings:
            missing_str = ", ".join(result.missing_headings)
            problems.append(f"missing headings: {missing_str}")
        raise ValidationError("; ".join(problems))

    if args.summary_json:
        summary_payload = SummaryContract.from_mapping(
            _load_summary_json(args.summary_json)
        ).to_dict()
        claims_ok = _validate_claims(
            summary_payload,
            strict=args.strict_evidence,
        )
        if not claims_ok and not args.strict_evidence:
            print(
                "[validator] warning: summary missing structured claims",
                file=sys.stderr,
            )

    print(
        "[validator] OK",
        f"path={result.pdf_path}",
        f"pages={result.page_count}",
        f"headings={len(headings)}",
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValidationError as exc:
        print(f"[validator] FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
