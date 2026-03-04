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
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")
_EXPECTED_RESPONSE_KEYS = {"report_file_id", "supervisor_passed", "request_id"}


@dataclass
class ValidationResult:
    path: Path
    source_file_id: str
    ok: bool
    supervisor_passed: bool | None
    report_file_id: str | None
    request_id: str | None
    issues: list[str]


@dataclass
class RunSummary:
    responses_dir: str
    total_files: int
    parsed_files: int
    passed_files: int
    failed_files: int
    success_rate: float
    allow_supervisor_fail: bool
    strict_keys: bool
    missing_expected_source_ids: list[str]
    unexpected_source_ids: list[str]
    duplicate_report_file_ids: list[str]
    duplicate_request_ids: list[str]
    expected_ids_match: bool
    unique_report_ids: bool
    unique_request_ids: bool
    min_success_rate: float | None
    min_success_rate_met: bool | None
    load_failures: list[str]

    @property
    def ok(self) -> bool:
        checks = [
            self.failed_files == 0,
            self.expected_ids_match,
            self.unique_report_ids,
            self.unique_request_ids,
        ]
        if self.min_success_rate_met is not None:
            checks.append(self.min_success_rate_met)
        return all(checks)

    def to_dict(self, *, results: list[ValidationResult]) -> dict[str, Any]:
        return {
            "responses_dir": self.responses_dir,
            "total_files": self.total_files,
            "parsed_files": self.parsed_files,
            "passed_files": self.passed_files,
            "failed_files": self.failed_files,
            "success_rate": round(self.success_rate, 4),
            "allow_supervisor_fail": self.allow_supervisor_fail,
            "strict_keys": self.strict_keys,
            "missing_expected_source_ids": self.missing_expected_source_ids,
            "unexpected_source_ids": self.unexpected_source_ids,
            "duplicate_report_file_ids": self.duplicate_report_file_ids,
            "duplicate_request_ids": self.duplicate_request_ids,
            "expected_ids_match": self.expected_ids_match,
            "unique_report_ids": self.unique_report_ids,
            "unique_request_ids": self.unique_request_ids,
            "min_success_rate": self.min_success_rate,
            "min_success_rate_met": self.min_success_rate_met,
            "load_failures": self.load_failures,
            "ok": self.ok,
            "results": [
                {
                    "file": result.path.name,
                    "source_file_id": result.source_file_id,
                    "ok": result.ok,
                    "supervisor_passed": result.supervisor_passed,
                    "report_file_id": result.report_file_id,
                    "request_id": result.request_id,
                    "issues": result.issues,
                }
                for result in results
            ],
        }


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
    parser.add_argument(
        "--expected-file-ids-file",
        type=Path,
        default=None,
        help=(
            "Optional newline-delimited file containing expected source file IDs "
            "(matching JSON filename stems)."
        ),
    )
    parser.add_argument(
        "--strict-keys",
        action="store_true",
        help=(
            "Require each response object to contain exactly "
            "report_file_id, supervisor_passed, request_id."
        ),
    )
    parser.add_argument(
        "--min-success-rate",
        type=float,
        default=None,
        help=(
            "Optional minimum pass rate threshold from 0.0 to 1.0. "
            "Example: --min-success-rate 0.95"
        ),
    )
    parser.add_argument(
        "--scorecard-out",
        type=Path,
        default=None,
        help="Optional path to write a machine-readable run scorecard JSON file.",
    )
    return parser.parse_args(argv)


def _load_expected_file_ids(path: Path) -> set[str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Unable to read expected IDs file {path}: {exc}") from exc

    expected_ids: set[str] = set()
    for line in raw.splitlines():
        token = line.strip()
        if not token or token.startswith("#"):
            continue
        if not _FILE_ID_RE.match(token):
            raise RuntimeError(
                f"Invalid source file ID {token!r} in expected IDs file {path}"
            )
        expected_ids.add(token)
    return expected_ids


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
    strict_keys: bool,
) -> ValidationResult:
    issues: list[str] = []
    source_file_id = path.stem
    if not _FILE_ID_RE.match(source_file_id):
        issues.append(f"invalid source file ID from filename stem: {source_file_id!r}")

    if strict_keys:
        payload_keys = set(payload.keys())
        missing_keys = sorted(_EXPECTED_RESPONSE_KEYS - payload_keys)
        unexpected_keys = sorted(payload_keys - _EXPECTED_RESPONSE_KEYS)
        if missing_keys:
            issues.append("missing keys: " + ",".join(missing_keys))
        if unexpected_keys:
            issues.append("unexpected keys: " + ",".join(unexpected_keys))

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
        source_file_id=source_file_id,
        ok=not issues,
        supervisor_passed=supervisor_passed,
        report_file_id=report_file_id,
        request_id=request_id,
        issues=issues,
    )


def _find_duplicates(values: list[str | None]) -> list[str]:
    counts = Counter(value for value in values if value)
    return sorted(value for value, count in counts.items() if count > 1)


def _write_scorecard(
    *,
    scorecard_path: Path,
    summary: RunSummary,
    results: list[ValidationResult],
) -> None:
    scorecard_path.parent.mkdir(parents=True, exist_ok=True)
    payload = summary.to_dict(results=results)
    scorecard_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
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

    if args.min_success_rate is not None and not 0.0 <= args.min_success_rate <= 1.0:
        print(
            "[live-regression] FAILED: --min-success-rate must be between 0.0 and 1.0",
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

    expected_source_ids: set[str] = set()
    if args.expected_file_ids_file is not None:
        try:
            expected_source_ids = _load_expected_file_ids(args.expected_file_ids_file)
        except RuntimeError as exc:
            print(f"[live-regression] FAILED: {exc}", file=sys.stderr)
            return 1

    results: list[ValidationResult] = []
    load_failures: list[str] = []
    for path in files:
        try:
            payload = _load_payload(path)
        except RuntimeError as exc:
            load_failures.append(path.name)
            print(f"[live-regression] FAIL {path.name}: {exc}", file=sys.stderr)
            continue
        results.append(
            _validate_payload(
                path,
                payload,
                allow_supervisor_fail=args.allow_supervisor_fail,
                strict_keys=args.strict_keys,
            )
        )

    failures = len(load_failures)
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

    actual_source_ids = {result.source_file_id for result in results}
    missing_expected_source_ids: list[str] = []
    unexpected_source_ids: list[str] = []
    expected_ids_match = True
    if expected_source_ids:
        missing_expected_source_ids = sorted(expected_source_ids - actual_source_ids)
        unexpected_source_ids = sorted(actual_source_ids - expected_source_ids)
        expected_ids_match = not (missing_expected_source_ids or unexpected_source_ids)
        if missing_expected_source_ids:
            print(
                "[live-regression] FAIL expected IDs missing from responses: "
                + ",".join(missing_expected_source_ids),
                file=sys.stderr,
            )
        if unexpected_source_ids:
            print(
                "[live-regression] FAIL unexpected response IDs: "
                + ",".join(unexpected_source_ids),
                file=sys.stderr,
            )

    duplicate_report_file_ids = _find_duplicates(
        [result.report_file_id for result in results]
    )
    duplicate_request_ids = _find_duplicates([result.request_id for result in results])

    if duplicate_report_file_ids:
        print(
            "[live-regression] FAIL duplicate report_file_id values: "
            + ",".join(duplicate_report_file_ids),
            file=sys.stderr,
        )
    if duplicate_request_ids:
        print(
            "[live-regression] FAIL duplicate request_id values: "
            + ",".join(duplicate_request_ids),
            file=sys.stderr,
        )

    total_files = len(files)
    failed_files = failures
    passed_files = total_files - failed_files
    success_rate = (passed_files / total_files) if total_files else 0.0

    min_success_rate_met: bool | None = None
    if args.min_success_rate is not None:
        min_success_rate_met = success_rate >= args.min_success_rate
        if not min_success_rate_met:
            print(
                (
                    "[live-regression] FAIL success rate below threshold: "
                    f"{success_rate:.2%} < {args.min_success_rate:.2%}"
                ),
                file=sys.stderr,
            )

    summary = RunSummary(
        responses_dir=str(responses_dir),
        total_files=total_files,
        parsed_files=len(results),
        passed_files=passed_files,
        failed_files=failed_files,
        success_rate=success_rate,
        allow_supervisor_fail=args.allow_supervisor_fail,
        strict_keys=args.strict_keys,
        missing_expected_source_ids=missing_expected_source_ids,
        unexpected_source_ids=unexpected_source_ids,
        duplicate_report_file_ids=duplicate_report_file_ids,
        duplicate_request_ids=duplicate_request_ids,
        expected_ids_match=expected_ids_match,
        unique_report_ids=not duplicate_report_file_ids,
        unique_request_ids=not duplicate_request_ids,
        min_success_rate=args.min_success_rate,
        min_success_rate_met=min_success_rate_met,
        load_failures=load_failures,
    )

    if args.scorecard_out is not None:
        try:
            _write_scorecard(
                scorecard_path=args.scorecard_out,
                summary=summary,
                results=results,
            )
            print(f"[live-regression] wrote scorecard: {args.scorecard_out}")
        except OSError as exc:
            print(
                f"[live-regression] FAILED: unable to write scorecard: {exc}",
                file=sys.stderr,
            )
            return 1

    if not summary.ok:
        print(
            (
                "[live-regression] FAILED: "
                f"passed={summary.passed_files}/{summary.total_files} "
                f"rate={summary.success_rate:.2%} "
                f"load_failures={len(summary.load_failures)}"
            ),
            file=sys.stderr,
        )
        return 1

    print(
        (
            "[live-regression] OK: "
            f"passed={summary.passed_files}/{summary.total_files} "
            f"rate={summary.success_rate:.2%} "
            f"in {responses_dir}."
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
