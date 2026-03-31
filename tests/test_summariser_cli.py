from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from src.models.summary_contract import SummaryContract
from src.services import summariser_refactored as ref_mod


def test_refactored_cli_dry_run(tmp_path) -> None:
    output_path = tmp_path / "summary.json"
    ref_mod._cli(
        [
            "--input",
            str(Path("tests/fixtures/sample_ocr.json")),
            "--output",
            str(output_path),
            "--dry-run",
        ]
    )
    assert output_path.exists()
    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == "2025-10-01"
    assert data["sections"]
    assert "Medical Summary" not in data
    assert "Patient Information" not in data
    assert "Billing Highlights" not in data
    assert "Legal / Notes" not in data
    assert data["metadata"]["summary_strategy_requested"] == "auto"
    assert data["metadata"]["summary_strategy_used"] == "one_shot"
    contract = SummaryContract.from_mapping(data)
    medical_summary = contract.as_text()
    assert len(medical_summary) >= 400
    assert "Document processed in " not in medical_summary
    assert "Provider Seen:" in medical_summary
    assert "Reason for Visit:" in medical_summary
    assert "Clinical Findings:" in medical_summary


def test_refactored_cli_dry_run_routes_large_payload_to_chunked(tmp_path) -> None:
    payload_path = tmp_path / "large_ocr.json"
    output_path = tmp_path / "summary.json"
    large_text = (
        "Patient seen by Dr. Provider1 for lumbar pain, diagnosis DX-01, and medication Med-01. "
        "Physical therapy phase 1 continues with reassessment planned in six weeks. "
    ) * 500
    payload_path.write_text(
        json.dumps({"text": large_text, "metadata": {"facility": "Riverside Clinic"}}),
        encoding="utf-8",
    )

    ref_mod._cli(
        [
            "--input",
            str(payload_path),
            "--output",
            str(output_path),
            "--dry-run",
            "--one-shot-token-threshold",
            "200",
        ]
    )

    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert data["metadata"]["summary_strategy_requested"] == "auto"
    assert data["metadata"]["summary_strategy_selected"] == "chunked"
    assert data["metadata"]["summary_strategy_used"] == "chunked"


def test_refactored_cli_logs_summary_artifact_context(monkeypatch, tmp_path) -> None:
    output_path = tmp_path / "summary.json"
    log_events: list[dict[str, object]] = []

    class _StubStateStore:
        def __init__(self) -> None:
            self.job = SimpleNamespace(
                trace_id="trace-123",
                request_id="req-123",
                object_uri="gs://mcc-intake/uploads/drive/input.pdf",
                object_name="uploads/drive/input.pdf",
                metadata={},
                retries={},
            )

        def get_job(self, job_id: str):
            assert job_id == "job-123"
            return self.job

        def mark_status(self, *_args, **_kwargs):
            return self.job

    monkeypatch.setattr(
        ref_mod, "create_state_store_from_env", lambda: _StubStateStore()
    )
    monkeypatch.setattr(
        ref_mod,
        "_upload_summary_to_gcs",
        lambda gcs_uri, _summary, if_generation_match=None: gcs_uri,
    )
    monkeypatch.setattr(
        ref_mod,
        "structured_log",
        lambda _logger, _level, event, **fields: log_events.append(
            {"event": event, "fields": fields}
        ),
    )

    ref_mod._cli(
        [
            "--input",
            str(Path("tests/fixtures/sample_ocr.json")),
            "--output",
            str(output_path),
            "--output-gcs",
            "gs://mcc-output/summaries/job-123.json",
            "--job-id",
            "job-123",
            "--dry-run",
        ]
    )

    summary_done = next(
        entry for entry in log_events if entry["event"] == "summary_done"
    )
    assert summary_done["fields"]["job_id"] == "job-123"
    assert summary_done["fields"]["trace_id"] == "trace-123"
    assert summary_done["fields"]["request_id"] == "req-123"
    assert summary_done["fields"]["stage"] == "SUMMARY_JOB"
    assert summary_done["fields"]["supervisor_passed"] is True
    assert summary_done["fields"]["object_uri"] == "gs://mcc-intake/uploads/drive/input.pdf"
    assert summary_done["fields"]["summary_uri"] == "gs://mcc-output/summaries/job-123.json"
    assert isinstance(summary_done["fields"]["duration_ms"], int)
    assert summary_done["fields"]["duration_ms"] >= 0
    assert isinstance(summary_done["fields"]["summary_chars"], int)
    assert summary_done["fields"]["summary_chars"] > 0
