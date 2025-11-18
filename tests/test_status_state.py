import base64

import src.services.pipeline as pipeline_module
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
    serialized = pipeline_module.pipeline_job_to_dict(snapshot)
    assert serialized["status"] == PipelineStatus.UPLOADED.value
    assert serialized["history"][-1]["stage"] == "PDF_JOB"


def test_get_by_dedupe_supports_hashless_lookup():
    store = InMemoryStateStore()
    job = store.create_job(
        PipelineJobCreate(
            bucket="intake",
            object_name="docs/file.pdf",
            generation="2",
            md5_hash=None,
        )
    )
    hashless_key = job.dedupe_key.split("#", 1)[0]
    lookup = store.get_by_dedupe(hashless_key)
    assert lookup is not None
    assert lookup.job_id == job.job_id


def test_normalise_hash_component_handles_base64_and_noise():
    decoded = pipeline_module._normalise_hash_component(
        base64.b64encode(b"hash-value").decode("ascii"), "seed"
    )
    assert decoded.startswith(b"hash-value".hex())
    fallback = pipeline_module._normalise_hash_component(" deadBEEF!!", "seed")
    assert fallback == "deadbeef"


def test_create_state_store_gcs(monkeypatch):
    monkeypatch.setenv("PIPELINE_STATE_BACKEND", "gcs")
    monkeypatch.setenv("PIPELINE_STATE_BUCKET", "state-bucket")
    monkeypatch.setenv("PIPELINE_STATE_PREFIX", "prefix")
    monkeypatch.setenv("PROJECT_ID", "proj")

    def _fake_resolve(name: str, project_id: str | None = None) -> str | None:
        if name == "PIPELINE_STATE_KMS_KEY":
            return "kms"
        return None

    monkeypatch.setattr(pipeline_module, "resolve_secret_env", _fake_resolve)
    created_kwargs: dict[str, object] = {}

    def _fake_gcs(**kwargs):
        created_kwargs.update(kwargs)
        return "gcs-store"

    monkeypatch.setattr(pipeline_module, "GCSStateStore", _fake_gcs)
    store = pipeline_module.create_state_store_from_env()
    assert store == "gcs-store"
    assert created_kwargs["bucket"] == "state-bucket"
    assert created_kwargs["prefix"] == "prefix"
    assert created_kwargs["kms_key_name"] == "kms"


def test_create_workflow_launcher_prefers_cloud(monkeypatch):
    monkeypatch.setenv("PIPELINE_WORKFLOW_NAME", "projects/p/workflows/wf")
    created: list[str] = []

    class _StubLauncher:
        def __init__(self, workflow_name: str) -> None:
            created.append(workflow_name)

    monkeypatch.setattr(
        pipeline_module, "CloudWorkflowsLauncher", _StubLauncher
    )
    launcher = pipeline_module.create_workflow_launcher_from_env()
    assert isinstance(launcher, _StubLauncher)
    assert created == ["projects/p/workflows/wf"]


def test_extract_trace_id_handles_empty_and_populated_headers():
    assert pipeline_module.extract_trace_id(None) is None
    assert pipeline_module.extract_trace_id("abc123/456;o=1") == "abc123"
