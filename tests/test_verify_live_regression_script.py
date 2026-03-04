from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_live_regression.py"


def _run_verify(
    responses_dir: Path, extra_args: list[str] | None = None
) -> subprocess.CompletedProcess[str]:
    args = [
        sys.executable,
        str(SCRIPT),
        "--responses-dir",
        str(responses_dir),
    ]
    if extra_args:
        args.extend(extra_args)
    return subprocess.run(args, capture_output=True, text=True, check=False)


def test_verify_live_regression_success(tmp_path: Path) -> None:
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    (responses_dir / "one.json").write_text(
        json.dumps(
            {
                "report_file_id": "1ZFra9EN0jS8wTS4dcW7deypxnVggb8vS",
                "supervisor_passed": True,
                "request_id": "req-1",
            }
        ),
        encoding="utf-8",
    )
    (responses_dir / "two.json").write_text(
        json.dumps(
            {
                "report_file_id": "1x7dYpS6yD5eN4mK3jH2gF1cB0aZ9qW8",
                "supervisor_passed": True,
                "request_id": "req-2",
            }
        ),
        encoding="utf-8",
    )

    result = _run_verify(responses_dir, ["--expect-count", "2"])
    assert result.returncode == 0, result.stderr
    assert "[live-regression] OK" in result.stdout


def test_verify_live_regression_fails_without_allow_flag(tmp_path: Path) -> None:
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    (responses_dir / "fail.json").write_text(
        json.dumps(
            {
                "report_file_id": "1ZFra9EN0jS8wTS4dcW7deypxnVggb8vS",
                "supervisor_passed": False,
                "request_id": "req-3",
            }
        ),
        encoding="utf-8",
    )

    result = _run_verify(responses_dir)
    assert result.returncode != 0
    assert "supervisor_passed=false" in (result.stderr + result.stdout)


def test_verify_live_regression_allows_supervisor_fail_when_requested(
    tmp_path: Path,
) -> None:
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    (responses_dir / "warn.json").write_text(
        json.dumps(
            {
                "report_file_id": "1ZFra9EN0jS8wTS4dcW7deypxnVggb8vS",
                "supervisor_passed": False,
                "request_id": "req-4",
            }
        ),
        encoding="utf-8",
    )

    result = _run_verify(responses_dir, ["--allow-supervisor-fail"])
    assert result.returncode == 0, result.stderr


def test_verify_live_regression_fails_on_invalid_report_id(tmp_path: Path) -> None:
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    (responses_dir / "bad-id.json").write_text(
        json.dumps(
            {
                "report_file_id": "short",
                "supervisor_passed": True,
                "request_id": "req-5",
            }
        ),
        encoding="utf-8",
    )

    result = _run_verify(responses_dir)
    assert result.returncode != 0
    assert "invalid report_file_id format" in (result.stderr + result.stdout)
