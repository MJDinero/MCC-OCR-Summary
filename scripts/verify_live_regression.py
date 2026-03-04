#!/usr/bin/env python3
"""Validate saved /process/drive live-regression responses.

This script is intentionally read-only: it never triggers pipeline runs or cloud writes.
It validates response JSON files produced by manual live-regression invocations.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")


@dataclass
class ValidationResult:
    path: Path
    ok: bool
    supervisor_passed: bool | None
    report_file_id: str | None
    request_id: str | None
    issues: list[str]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate saved live-regression JSON responses from GET /process/drive."
        )
    )
    parser.add_argument(
        "--responses-dir",
        required=True,
        type=Path,
        help="Directory containing JSON responses captured from /process/drive.",
    )
    parser.add_argument(
        "--expect-count",
        type=int,
        default=None,
        help="Optional expected number of JSON response files.",
    )
    parser.add_argument(
        "--allow-supervisor-fail",
        action="store_true",
        help="Allow supervisor_passed=false without failing the run.",
    )
    return parser.parse_args(argv)


def _load_payload(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Unable to read {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} must contain a JSON object.")
    return data


def _validate_payload(
    path: Path,
    payload: dict[str, Any],
    *,
    allow_supervisor_fail: bool,
) -> ValidationResult:
    issues: list[str] = []

    report_file_id_raw = payload.get("report_file_id")
    report_file_id = str(report_file_id_raw).strip() if report_file_id_raw else None
    if not report_file_id:
        issues.append("missing report_file_id")
    elif not _FILE_ID_RE.match(report_file_id):
        issues.append(f"invalid report_file_id format: {report_file_id!r}")

    supervisor_raw = payload.get("supervisor_passed")
    supervisor_passed: bool | None
    if isinstance(supervisor_raw, bool):
        supervisor_passed = supervisor_raw
    else:
        supervisor_passed = None
        issues.append("missing boolean supervisor_passed")

    if supervisor_passed is False and not allow_supervisor_fail:
        issues.append("supervisor_passed=false")

    request_raw = payload.get("request_id")
    request_id = str(request_raw).strip() if request_raw else None
    if not request_id:
        issues.append("missing request_id")

    return ValidationResult(
        path=path,
        ok=not issues,
        supervisor_passed=supervisor_passed,
        report_file_id=report_file_id,
        request_id=request_id,
        issues=issues,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    responses_dir: Path = args.responses_dir

    if not responses_dir.exists() or not responses_dir.is_dir():
        print(
            f"[live-regression] FAILED: responses directory not found: {responses_dir}",
            file=sys.stderr,
        )
        return 1

    files = sorted(path for path in responses_dir.glob("*.json") if path.is_file())
    if not files:
        print(
            f"[live-regression] FAILED: no JSON responses found in {responses_dir}",
            file=sys.stderr,
        )
        return 1

    if args.expect_count is not None and len(files) != args.expect_count:
        print(
            (
                "[live-regression] FAILED: expected "
                f"{args.expect_count} response files but found {len(files)}"
            ),
            file=sys.stderr,
        )
        return 1

    results: list[ValidationResult] = []
    failed_loads = 0
    for path in files:
        try:
            payload = _load_payload(path)
        except RuntimeError as exc:
            failed_loads += 1
            print(f"[live-regression] FAIL {path.name}: {exc}", file=sys.stderr)
            continue
        results.append(
            _validate_payload(
                path,
                payload,
                allow_supervisor_fail=args.allow_supervisor_fail,
            )
        )

    failures = failed_loads
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        details = (
            f"supervisor_passed={result.supervisor_passed} "
            f"report_file_id={result.report_file_id or '-'} "
            f"request_id={result.request_id or '-'}"
        )
        print(f"[live-regression] {status} {result.path.name}: {details}")
        if result.issues:
            failures += 1
            print(
                "[live-regression]   issues: " + "; ".join(result.issues),
                file=sys.stderr,
            )

    if failures:
        print(
            f"[live-regression] FAILED: {failures} response(s) did not meet criteria.",
            file=sys.stderr,
        )
        return 1

    print(
        f"[live-regression] OK: validated {len(results)} response file(s) in {responses_dir}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
