import json
import shlex
import shutil
import subprocess
from pathlib import Path


SCRIPT_PATH = Path("scripts/e2e_smoke.sh").resolve()


def _source_script(
    snippet: str, *, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    command = (
        "set -euo pipefail; "
        "export E2E_SMOKE_SOURCE_ONLY=1; "
        f"source {shlex.quote(str(SCRIPT_PATH))}; "
        f"{snippet}"
    )
    return subprocess.run(
        ["bash", "-lc", command],
        check=False,
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def test_e2e_smoke_help_prints_usage() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Usage: scripts/e2e_smoke.sh" in result.stdout
    assert "--drive-output-folder-id" in result.stdout
    assert "--state-bucket" in result.stdout


def test_e2e_smoke_dry_run_prints_plan() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH), "--dry-run"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "DRY RUN ONLY" in result.stdout
    assert "NON-PHI" in result.stdout
    assert "gcloud scheduler jobs run" in result.stdout
    assert "gcloud workflows executions list" in result.stdout
    assert "gs://mcc-output/summaries/<job_id>.json" in result.stdout
    assert "gs://mcc-output/pdf/<job_id>.pdf" in result.stdout
    assert (
        "gs://mcc-state-quantify-agent-us-central1-322786/pipeline-state/jobs/<job_id>.json"
        in result.stdout
    )
    assert "Validate summary JSON uses the refactored contract" in result.stdout
    assert "Verify Drive output in folder" in result.stdout


def test_e2e_smoke_enforces_live_run_guard() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 3
    assert "Blocked: set CONFIRM_LIVE_RUN=1 to run cloud actions." in result.stderr


def test_require_cmd_accepts_present_binary() -> None:
    result = _source_script("require_cmd bash; echo ok")

    assert result.returncode == 0
    assert result.stdout.strip() == "ok"


def test_require_cmd_rejects_missing_binary() -> None:
    result = _source_script("require_cmd definitely_missing_codex_command")

    assert result.returncode == 2
    assert (
        "Missing required command: definitely_missing_codex_command"
        in result.stderr
    )


def test_resolve_python_prefers_repo_venv(tmp_path: Path) -> None:
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\nexit 0\n")
    venv_python.chmod(0o755)

    result = _source_script("resolve_python", cwd=tmp_path)

    assert result.returncode == 0
    assert result.stdout.strip() == ".venv/bin/python"


def test_resolve_python_falls_back_to_system_python(tmp_path: Path) -> None:
    expected = "python3" if shutil.which("python3") else "python"

    result = _source_script("resolve_python", cwd=tmp_path)

    assert result.returncode == 0
    assert result.stdout.strip() == expected


def test_build_artifact_uri_helpers_are_deterministic() -> None:
    result = _source_script(
        'build_summary_uri "bucket-name" "job-123"; '
        'build_pdf_uri "bucket-name" "job-123"; '
        'build_report_name "job-123"; '
        'build_state_uri "state-bucket" "prefix" "job-123"'
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "gs://bucket-name/summaries/job-123.json",
        "gs://bucket-name/pdf/job-123.pdf",
        "summary-job-123.pdf",
        "gs://state-bucket/prefix/jobs/job-123.json",
    ]


def test_query_drive_output_files_builds_expected_request(tmp_path: Path) -> None:
    trace_file = tmp_path / "curl-args.txt"
    fake_curl = tmp_path / "bin" / "curl"
    fake_curl.parent.mkdir(parents=True)
    fake_curl.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$@\" > \"$TRACE_FILE\"\n"
        "printf '%s' '{\"files\":[]}'\n"
    )
    fake_curl.chmod(0o755)

    result = _source_script(
        "export TRACE_FILE="
        f"{shlex.quote(str(trace_file))}; "
        f"export PATH={shlex.quote(str(fake_curl.parent))}:$PATH; "
        'query_drive_output_files "token-1" "folder-1" '
        '"2026-03-06T03:00:00Z" "summary-job-123.pdf"',
        cwd=tmp_path,
    )

    assert result.returncode == 0
    assert result.stdout == '{"files":[]}'

    captured_args = trace_file.read_text().splitlines()
    assert "-sS" in captured_args
    assert "--get" in captured_args
    assert "Authorization: Bearer token-1" in captured_args
    assert any(
        "folder-1" in arg
        and "createdTime > '2026-03-06T03:00:00Z'" in arg
        and "name='summary-job-123.pdf'" in arg
        for arg in captured_args
    )
    assert "https://www.googleapis.com/drive/v3/files" in captured_args


def test_workflow_argument_matcher_supports_top_level_drive_file_id() -> None:
    payload = json.dumps(
        {
            "job_id": "job-123",
            "drive_file_id": "drive-input-123",
        }
    )

    result = _source_script(
        "payload="
        f"{shlex.quote(payload)}; "
        'workflow_argument_matches_drive_file_id "$payload" "drive-input-123"'
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "true"


def test_workflow_argument_matcher_supports_legacy_nested_drive_file_id() -> None:
    payload = json.dumps(
        {
            "job_id": "job-123",
            "metadata": {"drive_file_id": "drive-input-123"},
        }
    )

    result = _source_script(
        "payload="
        f"{shlex.quote(payload)}; "
        'workflow_argument_matches_drive_file_id "$payload" "drive-input-123"'
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "true"


def test_extract_single_drive_output_returns_unique_file() -> None:
    payload = json.dumps(
        {
            "files": [
                {
                    "id": "drive-file-id",
                    "name": "summary-proof.pdf",
                    "createdTime": "2026-03-06T03:01:02Z",
                }
            ]
        }
    )

    result = _source_script(
        f"payload={shlex.quote(payload)}; extract_single_drive_output \"$payload\""
    )

    assert result.returncode == 0
    assert (
        result.stdout.strip()
        == "drive-file-id\tsummary-proof.pdf\t2026-03-06T03:01:02Z"
    )


def test_extract_single_drive_output_rejects_ambiguous_results() -> None:
    payload = json.dumps(
        {
            "files": [
                {
                    "id": "drive-file-1",
                    "name": "summary-1.pdf",
                    "createdTime": "2026-03-06T03:01:02Z",
                },
                {
                    "id": "drive-file-2",
                    "name": "summary-2.pdf",
                    "createdTime": "2026-03-06T03:01:03Z",
                },
            ]
        }
    )

    result = _source_script(
        f"payload={shlex.quote(payload)}; extract_single_drive_output \"$payload\""
    )

    assert result.returncode == 2
    assert "Drive output query was ambiguous (2 files)." in result.stderr


def test_validate_refactored_summary_json_accepts_contract_payload() -> None:
    payload = json.dumps(
        {
            "schema_version": "2025-10-01",
            "sections": [
                {
                    "slug": "provider_seen",
                    "title": "Provider Seen",
                    "content": "Dr Example",
                    "ordinal": 1,
                }
            ],
        }
    )

    result = _source_script(
        "payload="
        f"{shlex.quote(payload)}; "
        'validate_refactored_summary_json "$payload"; echo ok'
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "ok"


def test_validate_refactored_summary_json_rejects_legacy_top_level_medical_summary() -> None:
    payload = json.dumps(
        {
            "schema_version": "2025-10-01",
            "sections": [
                {
                    "slug": "provider_seen",
                    "title": "Provider Seen",
                    "content": "Dr Example",
                    "ordinal": 1,
                }
            ],
            "Medical Summary": "Legacy narrative",
        }
    )

    result = _source_script(
        "payload="
        f"{shlex.quote(payload)}; "
        'validate_refactored_summary_json "$payload"'
    )

    assert result.returncode == 1
    assert "Summary JSON does not match the refactored contract." in result.stderr


def test_validate_refactored_summary_json_rejects_legacy_chunk_marker() -> None:
    payload = json.dumps(
        {
            "schema_version": "2025-10-01",
            "sections": [
                {
                    "slug": "provider_seen",
                    "title": "Provider Seen",
                    "content": "Document processed in 3 chunk(s)",
                    "ordinal": 1,
                }
            ],
        }
    )

    result = _source_script(
        "payload="
        f"{shlex.quote(payload)}; "
        'validate_refactored_summary_json "$payload"'
    )

    assert result.returncode == 1
    assert "Summary JSON still contains legacy chunk marker text." in result.stderr


def test_extract_summary_contract_metrics_returns_schema_and_section_count() -> None:
    payload = json.dumps(
        {
            "schema_version": "2025-10-01",
            "sections": [
                {
                    "slug": "provider_seen",
                    "title": "Provider Seen",
                    "content": "Dr Example",
                    "ordinal": 1,
                },
                {
                    "slug": "diagnoses",
                    "title": "Diagnoses",
                    "content": "Hypertension",
                    "ordinal": 2,
                },
            ],
        }
    )

    result = _source_script(
        "payload="
        f"{shlex.quote(payload)}; "
        'extract_summary_contract_metrics "$payload"'
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "2025-10-01\t2"


def test_print_success_summary_is_deterministic() -> None:
    result = _source_script(
        'print_success_summary '
        '"input-1" "synthetic.pdf" '
        '"projects/p/executions/e-1" "SUCCEEDED" '
        '"2026-03-06T02:49:09Z" "2026-03-06T02:55:58Z" '
        '"job-123" '
        '"gs://bucket/summaries/job-123.json" '
        '"gs://bucket/pdf/job-123.pdf" '
        '"gs://state-bucket/prefix/jobs/job-123.json" '
        '"2025-10-01" "7" '
        '"output-1" "summary-out.pdf" "2026-03-06T02:56:00Z"'
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "status=SMOKE_E2E_OK",
        "drive_input_file_id=input-1",
        "drive_input_file_name=synthetic.pdf",
        "workflow_execution=projects/p/executions/e-1",
        "workflow_state=SUCCEEDED",
        "workflow_start=2026-03-06T02:49:09Z",
        "workflow_end=2026-03-06T02:55:58Z",
        "job_id=job-123",
        "summary_uri=gs://bucket/summaries/job-123.json",
        "pdf_uri=gs://bucket/pdf/job-123.pdf",
        "state_uri=gs://state-bucket/prefix/jobs/job-123.json",
        "summary_schema_version=2025-10-01",
        "summary_sections=7",
        "report_file_id=output-1",
        "drive_output_file_id=output-1",
        "drive_output_file_name=summary-out.pdf",
        "drive_output_created_time=2026-03-06T02:56:00Z",
    ]


def test_cleanup_tmp_dir_ignores_missing_variable() -> None:
    result = _source_script("unset E2E_SMOKE_TMP_DIR || true; cleanup_tmp_dir; echo ok")

    assert result.returncode == 0
    assert result.stdout.strip() == "ok"


def test_cleanup_tmp_dir_removes_registered_directory(tmp_path: Path) -> None:
    target_dir = tmp_path / "cleanup-target"
    target_dir.mkdir()
    (target_dir / "proof.txt").write_text("non-phi\n")

    result = _source_script(
        "E2E_SMOKE_TMP_DIR="
        f"{shlex.quote(str(target_dir))}; "
        "cleanup_tmp_dir; "
        '[[ ! -d "$E2E_SMOKE_TMP_DIR" ]] && echo removed',
        cwd=tmp_path,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "removed"
    assert not target_dir.exists()


def test_e2e_smoke_avoids_nonportable_mapfile() -> None:
    script_text = SCRIPT_PATH.read_text()
    assert "mapfile" not in script_text
