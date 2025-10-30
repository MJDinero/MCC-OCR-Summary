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
