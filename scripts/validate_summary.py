#!/usr/bin/env python3
"""Lightweight PDF validator for MCC summaries.

The script verifies that the rendered PDF has the expected number of pages and
that every MCC heading is present at least once in the extracted text. It is
intended to be used as a fast preflight guard before running larger pipelines.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

try:
    from pypdf import PdfReader  # type: ignore
except Exception as exc:  # pragma: no cover - import guard for helpful error
    raise SystemExit(f"Failed to import pypdf: {exc}") from exc


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
    return parser.parse_args(argv)


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
