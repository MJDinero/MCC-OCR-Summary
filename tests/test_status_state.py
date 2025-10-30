from src.services.pipeline import (
    InMemoryStateStore,
    PipelineJobCreate,
    PipelineStatus,
    job_public_view,
)


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
