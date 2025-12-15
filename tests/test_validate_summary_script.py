from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_summary.py"
PDF_SAMPLE = ROOT / "tests" / "fixtures" / "validator_sample.pdf"
SUMMARY_GOOD = ROOT / "tests" / "fixtures" / "summary_with_claims.json"
SUMMARY_BAD = ROOT / "tests" / "fixtures" / "summary_with_bad_claims.json"


def _run_validator(args: list[str]) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(SCRIPT), "--pdf-path", str(PDF_SAMPLE), *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_validator_success_case() -> None:
    result = _run_validator(["--expected-pages", "1"])
    assert result.returncode == 0, result.stderr
    assert "[validator] OK" in result.stdout


def test_validator_fails_on_page_mismatch() -> None:
    result = _run_validator(["--expected-pages", "2"])
    assert result.returncode != 0
    assert "expected 2 pages" in result.stderr + result.stdout


def test_validator_detects_missing_heading() -> None:
    result = _run_validator(
        ["--expected-pages", "1", "--required-heading", "Imaginary Heading"]
    )
    assert result.returncode != 0
    assert "missing headings" in result.stderr + result.stdout


def test_validator_claims_pass() -> None:
    result = _run_validator(
        ["--expected-pages", "1", "--summary-json", str(SUMMARY_GOOD)]
    )
    assert result.returncode == 0


def test_validator_claims_warn_without_evidence() -> None:
    result = _run_validator(
        ["--expected-pages", "1", "--summary-json", str(SUMMARY_BAD)]
    )
    assert result.returncode == 0
    assert "warning" in (result.stderr + result.stdout).lower()


def test_validator_claims_fail_when_strict() -> None:
    result = _run_validator(
        [
            "--expected-pages",
            "1",
            "--summary-json",
            str(SUMMARY_BAD),
            "--strict-evidence",
        ]
    )
    assert result.returncode != 0
    assert "missing" in (result.stderr + result.stdout).lower()
