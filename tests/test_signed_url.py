import json
from pathlib import Path
from tempfile import TemporaryDirectory

from src.services.pdf_writer_refactored import _cli as pdf_cli
from src.services.pipeline import InMemoryStateStore, PipelineJobCreate, PipelineStatus


class _StubBlob:
    def __init__(self):
        self.bucket = type("Bucket", (), {"name": "output"})()
        self.name = "pdf/job.pdf"


def test_pdf_writer_marks_uploaded_with_signed_url(monkeypatch):
    store = InMemoryStateStore()
    job = store.create_job(
        PipelineJobCreate(
            bucket="intake",
            object_name="docs/file.pdf",
            generation="1",
            md5_hash="hash==",
        )
    )

    monkeypatch.setattr(
        "src.services.pdf_writer_refactored.create_state_store_from_env", lambda: store
    )

    uploads: list[bytes] = []

    def _fake_upload(pdf_bytes, gcs_uri, **_):
        uploads.append(pdf_bytes)
        return _StubBlob()

    monkeypatch.setattr(
        "src.services.pdf_writer_refactored._upload_pdf_to_gcs", _fake_upload
    )
    monkeypatch.setattr(
        "src.services.pdf_writer_refactored._generate_signed_url",
        lambda blob, ttl_seconds, kms_key=None: "https://signed.example/pdfs",
    )

    with TemporaryDirectory() as tmp_dir:
        summary_path = Path(tmp_dir) / "summary.json"
        summary_payload = {
            "Patient Information": "N/A",
            "Medical Summary": "Example summary text",
            "Billing Highlights": "N/A",
            "Legal / Notes": "N/A",
        }
        summary_path.write_text(json.dumps(summary_payload), encoding="utf-8")

        pdf_cli(
            [
                "--input",
                str(summary_path),
                "--upload-gcs",
                "gs://output/pdf/job.pdf",
                "--job-id",
                job.job_id,
            ]
        )

    assert uploads, "expected upload to be invoked"
    updated = store.get_job(job.job_id)
    assert updated.status is PipelineStatus.UPLOADED
    assert updated.signed_url == "https://signed.example/pdfs"
    assert updated.history[-1]["status"] == PipelineStatus.UPLOADED.value


def test_pdf_writer_logs_summary_and_pdf_artifacts(monkeypatch, tmp_path):
    store = InMemoryStateStore()
    job = store.create_job(
        PipelineJobCreate(
            bucket="intake",
            object_name="docs/file.pdf",
            generation="1",
            md5_hash="hash==",
        )
    )
    monkeypatch.setattr(
        "src.services.pdf_writer_refactored.create_state_store_from_env", lambda: store
    )
    monkeypatch.setattr(
        "src.services.pdf_writer_refactored._load_summary",
        lambda _path: {
            "schema_version": "test",
            "sections": [
                {
                    "slug": "medical_summary",
                    "title": "Medical Summary",
                    "content": "Structured output",
                    "ordinal": 1,
                    "kind": "narrative",
                }
            ],
            "_claims": [],
            "_evidence_spans": [],
        },
    )
    monkeypatch.setattr(
        "src.services.pdf_writer_refactored._upload_pdf_to_gcs",
        lambda _pdf_bytes, _gcs_uri, **_: _StubBlob(),
    )

    log_events: list[dict[str, object]] = []
    monkeypatch.setattr(
        "src.services.pdf_writer_refactored.structured_log",
        lambda _logger, _level, event, **fields: log_events.append(
            {"event": event, "fields": fields}
        ),
    )

    pdf_cli(
        [
            "--input",
            "gs://mcc-output/summaries/job.json",
            "--output",
            str(tmp_path / "summary.pdf"),
            "--upload-gcs",
            "gs://output/pdf/job.pdf",
            "--skip-signed-url",
            "--job-id",
            job.job_id,
        ]
    )

    pdf_done = next(entry for entry in log_events if entry["event"] == "pdf_done")
    assert pdf_done["fields"]["job_id"] == job.job_id
    assert pdf_done["fields"]["trace_id"] == job.trace_id
    assert pdf_done["fields"]["request_id"] == job.request_id
    assert pdf_done["fields"]["stage"] == "PDF_JOB"
    assert pdf_done["fields"]["pdf_uri"] == "gs://output/pdf/job.pdf"
    assert pdf_done["fields"]["summary_uri"] == "gs://mcc-output/summaries/job.json"
    assert pdf_done["fields"]["object_uri"] == job.object_uri
    assert isinstance(pdf_done["fields"]["duration_ms"], int)
    assert pdf_done["fields"]["duration_ms"] >= 0
