import json

from src.services.pipeline import (
    InMemoryStateStore,
    PipelineJobCreate,
    PipelineStatus,
    job_public_view,
)
from src.utils.error_reporting import REDACTED_FAILURE_MESSAGE


def test_job_public_view_captures_retries_and_upload_metadata():
    store = InMemoryStateStore()
    job = store.create_job(
        PipelineJobCreate(
            bucket="intake",
            object_name="docs/file.pdf",
            generation="1",
            md5_hash="hash==",
            trace_id="trace",
        )
    )

    store.record_retry(job.job_id, "DOC_AI_OCR")
    store.mark_status(
        job.job_id,
        PipelineStatus.UPLOADED,
        stage="PDF_JOB",
        message="PDF uploaded",
        extra={"pdf_uri": "gs://output/docs/file.pdf"},
        updates={
            "pdf_uri": "gs://output/docs/file.pdf",
            "signed_url": "https://signed",
        },
    )

    snapshot = store.get_job(job.job_id)
    view = job_public_view(snapshot)
    assert view["status"] == PipelineStatus.UPLOADED.value
    assert view["retries"]["DOC_AI_OCR"] == 1
    assert view["pdf_uri"] == "gs://output/docs/file.pdf"
    assert view["signed_url"] == "https://signed"
    assert view["history"][-1]["stage"] == "PDF_JOB"


def test_job_public_view_redacts_failed_error_details():
    store = InMemoryStateStore()
    job = store.create_job(
        PipelineJobCreate(
            bucket="intake",
            object_name="docs/file.pdf",
            generation="1",
            trace_id="trace",
        )
    )

    sensitive_error = (
        "Patient Name: Jordan Carter failed supervisor validation for low back pain."
    )
    store.mark_status(
        job.job_id,
        PipelineStatus.FAILED,
        stage="SUMMARY_JOB",
        message=sensitive_error,
        extra={
            "error": sensitive_error,
            "error_type": "RuntimeError",
            "phase": "summarisation",
            "summary_backend": "chunked",
        },
        updates={
            "last_error": {
                "stage": "summary_upload",
                "error": sensitive_error,
                "error_type": "RuntimeError",
            }
        },
    )

    snapshot = store.get_job(job.job_id)
    assert snapshot is not None
    assert snapshot.last_error is not None
    assert snapshot.last_error["error"] == REDACTED_FAILURE_MESSAGE

    view = job_public_view(snapshot)
    assert view["history"][-1]["message"] == REDACTED_FAILURE_MESSAGE
    assert view["history"][-1]["extra"]["phase"] == "summarisation"
    assert view["history"][-1]["extra"]["summary_backend"] == "chunked"
    assert view["history"][-1]["extra"]["error_type"] == "RuntimeError"
    assert view["history"][-1]["extra"]["error_redacted"] is True
    assert view["last_error"]["stage"] == "summary_upload"
    assert view["last_error"]["error"] == REDACTED_FAILURE_MESSAGE
    assert "Jordan Carter" not in json.dumps(view)
