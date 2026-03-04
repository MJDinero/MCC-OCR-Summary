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
    scorecard_path = tmp_path / "scorecard.json"
    (responses_dir / "1ZFra9EN0jS8wTS4dcW7deypxnVggb8vS.json").write_text(
        json.dumps(
            {
                "report_file_id": "1ZFra9EN0jS8wTS4dcW7deypxnVggb8vS",
                "supervisor_passed": True,
                "request_id": "req-1",
            }
        ),
        encoding="utf-8",
    )
    (responses_dir / "1x7dYpS6yD5eN4mK3jH2gF1cB0aZ9qW8.json").write_text(
        json.dumps(
            {
                "report_file_id": "1x7dYpS6yD5eN4mK3jH2gF1cB0aZ9qW8",
                "supervisor_passed": True,
                "request_id": "req-2",
            }
        ),
        encoding="utf-8",
    )

    result = _run_verify(
        responses_dir,
        [
            "--expect-count",
            "2",
            "--strict-keys",
            "--scorecard-out",
            str(scorecard_path),
        ],
    )
    assert result.returncode == 0, result.stderr
    assert "[live-regression] OK" in result.stdout
    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
    assert scorecard["ok"] is True
    assert scorecard["passed_files"] == 2
    assert scorecard["success_rate"] == 1.0


def test_verify_live_regression_fails_without_allow_flag(tmp_path: Path) -> None:
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    (responses_dir / "1failresponse12345.json").write_text(
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
    (responses_dir / "1warnresponse12345.json").write_text(
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
    (responses_dir / "1badidresponse12345.json").write_text(
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


def test_verify_live_regression_fails_on_expected_id_set_mismatch(
    tmp_path: Path,
) -> None:
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    expected_ids_path = tmp_path / "expected_ids.txt"
    expected_ids_path.write_text(
        "\n".join(
            [
                "1ZFra9EN0jS8wTS4dcW7deypxnVggb8vS",
                "1x7dYpS6yD5eN4mK3jH2gF1cB0aZ9qW8",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (responses_dir / "1ZFra9EN0jS8wTS4dcW7deypxnVggb8vS.json").write_text(
        json.dumps(
            {
                "report_file_id": "1a2b3c4d5e6f7g8h9i0j",
                "supervisor_passed": True,
                "request_id": "req-1",
            }
        ),
        encoding="utf-8",
    )

    result = _run_verify(
        responses_dir,
        ["--expected-file-ids-file", str(expected_ids_path)],
    )
    assert result.returncode != 0
    combined = result.stderr + result.stdout
    assert "expected IDs missing from responses" in combined
    assert "1x7dYpS6yD5eN4mK3jH2gF1cB0aZ9qW8" in combined


def test_verify_live_regression_fails_on_duplicate_ids(tmp_path: Path) -> None:
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    shared_report_file_id = "1sharedreportidforvalidation12345"
    shared_request_id = "request-shared-id"
    (responses_dir / "1responseA12345.json").write_text(
        json.dumps(
            {
                "report_file_id": shared_report_file_id,
                "supervisor_passed": True,
                "request_id": shared_request_id,
            }
        ),
        encoding="utf-8",
    )
    (responses_dir / "1responseB67890.json").write_text(
        json.dumps(
            {
                "report_file_id": shared_report_file_id,
                "supervisor_passed": True,
                "request_id": shared_request_id,
            }
        ),
        encoding="utf-8",
    )

    result = _run_verify(responses_dir)
    assert result.returncode != 0
    combined = result.stderr + result.stdout
    assert "duplicate report_file_id values" in combined
    assert "duplicate request_id values" in combined
