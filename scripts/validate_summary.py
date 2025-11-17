#!/usr/bin/env python3
"""Validates the PDF contract produced by the MCC OCR summary pipeline."""

from __future__ import annotations

import argparse
import json
import os
import sys
from io import BytesIO
from pathlib import Path
from typing import Dict, Iterable, List, Tuple, Optional

import requests
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2 import service_account
from pypdf import PdfReader

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.services.bible import (
    CANONICAL_ENTITY_ORDER,
    CANONICAL_NARRATIVE_ORDER,
    CANONICAL_SECTION_ORDER,
    ENTITY_FORBIDDEN_TOKENS,
    FORBIDDEN_PDF_PHRASES,
)

EXPECTED_HEADINGS: Tuple[str, ...] = CANONICAL_SECTION_ORDER
NARRATIVE_HEADINGS = CANONICAL_NARRATIVE_ORDER
ENTITY_HEADINGS = CANONICAL_ENTITY_ORDER


def _build_drive_session(
    cred_path: str,
    *,
    impersonate: str | None = None,
) -> AuthorizedSession:
    if not os.path.exists(cred_path):
        raise FileNotFoundError(f"Credentials file not found: {cred_path}")
    scopes = (
        "https://www.googleapis.com/auth/drive",
    )
    credentials = service_account.Credentials.from_service_account_file(
        cred_path, scopes=scopes
    )
    if impersonate:
        credentials = credentials.with_subject(impersonate)
    return AuthorizedSession(credentials)


def _build_process_session(
    base_url: str,
    cred_path: str,
    *,
    impersonate: str | None = None,  # kept for API symmetry; ID tokens ignore it
):
    audience = base_url.rstrip("/")
    if not audience:
        raise ValueError("base_url required for process session")
    credentials = service_account.IDTokenCredentials.from_service_account_file(
        cred_path, target_audience=audience
    )
    credentials.refresh(Request())
    return AuthorizedSession(credentials)


def _download_drive_pdf(
    session: AuthorizedSession,
    *,
    file_id: str,
) -> bytes:
    resp = session.get(
        f"https://www.googleapis.com/drive/v3/files/{file_id}",
        params={"alt": "media"},
        timeout=120,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Drive download failed ({resp.status_code}): {resp.text[:200]}"
        )
    return resp.content


def _extract_lines(pdf_bytes: bytes) -> Tuple[List[str], int]:
    reader = PdfReader(BytesIO(pdf_bytes))
    lines: List[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        for raw in text.splitlines():
            cleaned = raw.strip()
            if cleaned:
                lines.append(cleaned)
    return lines, len(reader.pages)


def _count_pages(pdf_bytes: bytes) -> int:
    reader = PdfReader(BytesIO(pdf_bytes))
    return len(reader.pages)


def _segment_sections(lines: Iterable[str]) -> Tuple[List[str], Dict[str, List[str]]]:
    sections: Dict[str, List[str]] = {heading: [] for heading in EXPECTED_HEADINGS}
    order: List[str] = []
    current: str | None = None
    for line in lines:
        if line in EXPECTED_HEADINGS:
            current = line
            order.append(line)
            continue
        if current is None:
            continue
        sections[current].append(line)
    return order, sections


def _strip_bullet(value: str) -> str:
    return value.lstrip("•*- ").strip()


def _validate_entity_lines(section: str, lines: List[str]) -> None:
    cleaned = [_strip_bullet(line) for line in lines if line.strip()]
    if not cleaned:
        raise AssertionError(f"{section} empty after filtering")
    for raw in cleaned:
        if len(raw) < 3 or len(raw) > 220:
            raise AssertionError(f"{section} entry length invalid: {raw}")
        alpha = sum(1 for ch in raw if ch.isalpha())
        if alpha < 2:
            raise AssertionError(f"{section} entry missing alpha chars: {raw}")
        lowered = raw.lower()
        if any(token in lowered for token in ENTITY_FORBIDDEN_TOKENS):
            raise AssertionError(f"{section} entry contains forbidden token: {raw}")


def _validate_sections(order: List[str], sections: Dict[str, List[str]]) -> Dict[str, int]:
    if len(order) < len(EXPECTED_HEADINGS):
        missing = [heading for heading in EXPECTED_HEADINGS if heading not in order]
        raise AssertionError(f"Missing headings: {missing}")
    observed = order[: len(EXPECTED_HEADINGS)]
    if observed != list(EXPECTED_HEADINGS):
        raise AssertionError(
            f"Section heading order mismatch: observed={observed}, expected={EXPECTED_HEADINGS}"
        )
    stats: Dict[str, int] = {}
    for heading in EXPECTED_HEADINGS:
        body = sections.get(heading, [])
        if not body:
            raise AssertionError(f"{heading} is empty in PDF payload")
        stats[heading] = len(body)
    for entity in ENTITY_HEADINGS:
        _validate_entity_lines(entity, sections[entity])
    return stats


def _ensure_forbidden_absent(lines: Iterable[str]) -> None:
    blob = "\n".join(lines).lower()
    hits = [phrase for phrase in FORBIDDEN_PDF_PHRASES if phrase in blob]
    if hits:
        raise AssertionError(f"Forbidden phrases detected: {hits}")


def _trigger_process(
    base_url: str,
    *,
    file_id: str,
    timeout: int,
    session: requests.Session | AuthorizedSession,
) -> Dict[str, str]:
    base = base_url.rstrip("/")
    url = f"{base}/process/drive"
    response = session.get(url, params={"file_id": file_id}, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("report_file_id"):
        raise AssertionError(f"process/drive response missing report_file_id: {payload}")
    return payload


def _run(args: argparse.Namespace) -> None:
    pdf_bytes: Optional[bytes] = None
    metadata: Dict[str, str] = {}

    if args.pdf_path:
        pdf_path = Path(args.pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")
        pdf_bytes = pdf_path.read_bytes()
    else:
        if not args.report_file_id and not args.source_file_id:
            raise SystemExit("--source-file-id or --report-file-id must be provided")
        drive_session = _build_drive_session(
            args.credentials, impersonate=args.impersonate
        )
        process_session = _build_process_session(
            args.base_url, args.credentials, impersonate=args.impersonate
        )

        if args.expected_pages and args.source_file_id:
            source_pdf = _download_drive_pdf(
                drive_session,
                file_id=args.source_file_id,
            )
            source_page_count = _count_pages(source_pdf)
            if source_page_count != args.expected_pages:
                raise AssertionError(
                    f"Source PDF page count mismatch: got {source_page_count}, expected {args.expected_pages}"
                )

        report_file_id = args.report_file_id
        if not report_file_id:
            metadata = _trigger_process(
                args.base_url,
                file_id=args.source_file_id,
                timeout=args.timeout,
                session=process_session,
            )
            report_file_id = metadata.get("report_file_id")

        pdf_bytes = _download_drive_pdf(drive_session, file_id=report_file_id)

    if pdf_bytes is None:
        raise RuntimeError("Unable to load PDF bytes for validation")

    lines, page_count = _extract_lines(pdf_bytes)
    if args.expected_pages and args.pdf_path:
        if page_count != args.expected_pages:
            raise AssertionError(
                f"PDF page count mismatch: got {page_count}, expected {args.expected_pages}"
            )
    _ensure_forbidden_absent(lines)
    order, sections = _segment_sections(lines)
    stats = _validate_sections(order, sections)

    output = {
        "report_file_id": args.report_file_id,
        "page_count": page_count,
        "section_line_counts": stats,
        "trigger_metadata": metadata,
        "source": args.pdf_path or "drive",
    }
    print(json.dumps(output, indent=2))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the drive pipeline and validate the generated PDF contract."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("MCC_BASE_URL"),
        help="Base URL for the deployed service (e.g. https://demo-ocr-summary-xyz.a.run.app)",
    )
    parser.add_argument(
        "--source-file-id",
        help="Drive file id for the source OCR PDF to process.",
    )
    parser.add_argument(
        "--report-file-id",
        help="Existing Drive report file to validate (skip pipeline trigger).",
    )
    parser.add_argument(
        "--pdf-path",
        help="Local PDF path to validate (bypasses Drive + process calls).",
    )
    parser.add_argument(
        "--credentials",
        default=os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ""),
        help="Path to a service-account JSON with Drive read access.",
    )
    parser.add_argument(
        "--impersonate",
        help="Optional user to impersonate when using domain-wide delegation.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1200,
        help="Timeout in seconds for the /process/drive request.",
    )
    parser.add_argument(
        "--expected-pages",
        type=int,
        help="Expected number of pages in the source PDF (must match before validation proceeds).",
    )
    return parser


def main() -> None:  # pragma: no cover - integration helper
    parser = _build_parser()
    args = parser.parse_args()
    if not args.pdf_path:
        if not args.base_url:
            parser.error(
                "--base-url is required (set MCC_BASE_URL env var or pass flag)"
            )
        if not args.credentials:
            parser.error(
                "Missing --credentials (GOOGLE_APPLICATION_CREDENTIALS not set)"
            )
    try:
        _run(args)
    except Exception as exc:  # pylint: disable=broad-except
        print(f"VALIDATION FAILED: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":  # pragma: no cover
    main()
