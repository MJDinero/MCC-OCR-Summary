import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

import pytest
from fastapi.testclient import TestClient

from src.errors import DriveServiceError
from src.main import create_app
from src.services.pdf_writer import MinimalPDFBackend, PDFWriter
from src.services.pipeline import PipelineStatus
from src.services.summariser_refactored import ChunkSummaryBackend, RefactoredSummariser
from src.services import summariser_refactored
from tests.stubs.drive_stub import StubDriveService


class _StubWorkflowLauncher:
    def __init__(self, *, execution_name: str = "executions/test") -> None:
        self.execution_name = execution_name
        self.calls: List[Dict[str, Any]] = []

    def launch(self, *, job, parameters=None, trace_context=None):  # noqa: D401 - signature matches production launcher
        self.calls.append(
            {
                "job_id": job.job_id,
                "parameters": parameters or {},
                "trace_context": trace_context,
            }
        )
        return self.execution_name


class _FailingWorkflowLauncher:
    def __init__(self) -> None:
        self.job_ids: List[str] = []

    def launch(self, *, job, **_kwargs):
        self.job_ids.append(job.job_id)
        raise RuntimeError("workflow dispatch failed")


DEFAULT_OVERVIEW = "Follow-up visit reviewing lumbar strain recovery with good functional gains."
DEFAULT_KEY_POINTS = [
    "Patient reports steady decrease in lumbar pain intensity during daily activities.",
    "Recent lumbar MRI shows no acute fracture or recurrent disc herniation.",
]
DEFAULT_DETAILS = [
    "Physical examination demonstrates improved flexion with minimal paraspinal tenderness.",
    "Core strengthening therapy sessions focus on stability drills and posture correction.",
]
DEFAULT_CARE_PLAN = [
    "Continue physical therapy twice weekly with emphasis on core stabilization exercises.",
    "Use ibuprofen 400 mg as needed for breakthrough discomfort and reassess in six weeks.",
]


class _DeterministicBackend(ChunkSummaryBackend):
    def summarise_chunk(
        self,
        *,
        chunk_text: str,
        chunk_index: int,
        total_chunks: int,
        estimated_tokens: int,
    ) -> Dict[str, List[str]]:
        return {
            "overview": DEFAULT_OVERVIEW,
            "key_points": list(DEFAULT_KEY_POINTS),
            "clinical_details": list(DEFAULT_DETAILS),
            "care_plan": list(DEFAULT_CARE_PLAN),
            "diagnoses": ["Lumbar strain, improving"],
            "providers": ["Dr. Leslie Carter MD"],
            "medications": ["Ibuprofen 400 mg as needed"],
        }


def _set_env(monkeypatch):
    monkeypatch.setenv("PROJECT_ID", "proj")
    monkeypatch.setenv("REGION", "us")
    monkeypatch.setenv("DOC_AI_PROCESSOR_ID", "pid")
    monkeypatch.setenv("DOC_AI_SPLITTER_PROCESSOR_ID", "split")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("DRIVE_INPUT_FOLDER_ID", "in")
    monkeypatch.setenv("DRIVE_REPORT_FOLDER_ID", "out")
    monkeypatch.setenv("INTAKE_GCS_BUCKET", "intake-test")
    monkeypatch.setenv("OUTPUT_GCS_BUCKET", "output-test")
    monkeypatch.setenv("SUMMARY_BUCKET", "summary-test")
    monkeypatch.setenv("PIPELINE_STATE_BACKEND", "memory")
    monkeypatch.setenv("SUMMARISER_JOB_NAME", "job-summary")
    monkeypatch.setenv("PDF_JOB_NAME", "job-pdf")
    monkeypatch.setenv("INTERNAL_EVENT_TOKEN", "secret-token")
    monkeypatch.setenv("SUMMARY_SCHEMA_VERSION", "2025-10-01")
    monkeypatch.setenv("PIPELINE_DLQ_TOPIC", "projects/proj/topics/dlq")


def _build_app_with_launcher(monkeypatch, launcher):
    _set_env(monkeypatch)
    monkeypatch.delenv("STUB_MODE", raising=False)
    app = create_app()
    app.state.workflow_launcher = launcher
    return app


def _ingest_payload(suffix: str = "") -> Dict[str, Any]:
    name = f"drive/file{suffix}.pdf"
    return {
        "object": {
            "bucket": "intake-test",
            "name": name,
            "generation": "1",
            "metageneration": "1",
            "size": 2048,
            "md5Hash": "hash==",
        },
        "source": "drive-webhook",
        "trace_id": f"trace-{suffix or 'seed'}",
    }


def _internal_headers():
    return {"X-Internal-Event-Token": "secret-token"}


def _build_process_app(monkeypatch, *, ocr_text: Optional[str] = None) -> Tuple[TestClient, StubDriveService, Dict[str, bytes]]:
    _set_env(monkeypatch)
    monkeypatch.setenv("STUB_MODE", "true")
    monkeypatch.setenv("MIN_OCR_CHARS", "10")
    monkeypatch.setenv("WRITE_TO_DRIVE", "true")
    app = create_app()
    app.state.supervisor_simple = True

    backend = _DeterministicBackend()
    app.state.summariser = RefactoredSummariser(
        backend=backend,
        target_chars=360,
        max_chars=480,
        overlap_chars=60,
        min_summary_chars=300,
    )
    app.state.pdf_writer = PDFWriter(MinimalPDFBackend(), title="Document Summary")

    original_summarise = app.state.summariser.summarise  # type: ignore[attr-defined]
    original_summarise_async = app.state.summariser.summarise_async  # type: ignore[attr-defined]

    def _clean_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
        cleaned = {k: v for k, v in summary.items() if not k.startswith("_")}
        return cleaned

    def _summarise_wrapper(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        summary = original_summarise(*args, **kwargs)
        return _clean_summary(dict(summary))

    async def _summarise_async_wrapper(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        summary = await original_summarise_async(*args, **kwargs)
        return _clean_summary(dict(summary))

    app.state.summariser.summarise = _summarise_wrapper  # type: ignore[attr-defined]
    app.state.summariser.summarise_async = _summarise_async_wrapper  # type: ignore[attr-defined]

    class _StubOCRService:
        def __init__(self, payload: str) -> None:
            self._payload = payload

        def process(self, _pdf_bytes: bytes, **_kwargs: Any) -> Dict[str, Any]:
            return {
                "text": self._payload,
                "pages": [{"page_number": 1, "text": self._payload}],
            }

    source_text = ocr_text or " ".join(
        [
            DEFAULT_OVERVIEW,
            *DEFAULT_KEY_POINTS,
            *DEFAULT_DETAILS,
            *DEFAULT_CARE_PLAN,
        ]
        * 3
    )
    app.state.ocr_service = _StubOCRService(source_text)

    drive_stub = StubDriveService()
    uploads: Dict[str, bytes] = {}

    def _download_pdf(file_id: str, **_: Any) -> bytes:
        if file_id not in drive_stub.files_store:
            raise DriveServiceError(f"File not found: {file_id}")
        return drive_stub.files_store[file_id]

    def _upload_pdf(file_bytes: bytes, folder_id: str | None = None, log_context: Dict[str, Any] | None = None) -> str:
        del folder_id, log_context
        file_id = f"uploaded-{len(uploads) + 1}"
        uploads[file_id] = file_bytes
        drive_stub.files_store[file_id] = file_bytes
        return file_id

    app.state.drive_client.download_pdf = _download_pdf  # type: ignore[attr-defined]
    app.state.drive_client.upload_pdf = _upload_pdf  # type: ignore[attr-defined]

    return TestClient(app), drive_stub, uploads


def test_pipeline_e2e_happy_path(monkeypatch):
    client, drive_stub, uploads = _build_process_app(monkeypatch)
    input_pdf = PDFWriter(MinimalPDFBackend(), title="Source Stub").build("Source document placeholder content")
    drive_stub.files_store["source-file"] = input_pdf

    response = client.get("/process/drive", params={"file_id": "source-file"})
    assert response.status_code == 200
    body = response.json()
    assert body["supervisor_passed"] is True
    uploaded_id = body["report_file_id"]
    assert uploaded_id in uploads

    pdf_bytes = uploads[uploaded_id]
    pdf_text = pdf_bytes.decode("utf-8", errors="ignore")
    extracted_lines = [line.strip() for line in re.findall(r"\(([^()]*)\)\sTj", pdf_text)]

    expected_headers = ["Intro Overview:", "Key Points:", "Detailed Findings:", "Care Plan & Follow-Up:"]
    for header in expected_headers:
        assert extracted_lines.count(header) == 1
    assert "Structured Indices" not in extracted_lines
    assert "(Condensed)" not in extracted_lines
    assert all("additional" not in line.lower() for line in extracted_lines)
    bullet_lines = [line for line in extracted_lines if line.startswith("- ")]
    assert bullet_lines, "Expected bullet list content in PDF output"


def test_pipeline_e2e_concurrent_ingest(monkeypatch):
    launcher = _StubWorkflowLauncher()
    app = _build_app_with_launcher(monkeypatch, launcher)
    client = TestClient(app)

    def _send(idx: int) -> str:
        resp = client.post("/ingest", json=_ingest_payload(suffix=str(idx)))
        assert resp.status_code == 202
        return resp.json()["job_id"]

    with ThreadPoolExecutor(max_workers=3) as executor:
        job_ids = list(executor.map(_send, range(3)))

    assert len(set(job_ids)) == 3
    store = app.state.state_store

    status_chain = [
        PipelineStatus.SPLIT_DONE,
        PipelineStatus.OCR_DONE,
        PipelineStatus.SUMMARY_DONE,
        PipelineStatus.PDF_DONE,
        PipelineStatus.COMPLETED,
    ]
    for job_id in job_ids:
        for status in status_chain:
            payload = {"status": status.value, "stage": status.name.lower()}
            resp = client.post(f"/ingest/internal/jobs/{job_id}/events", headers=_internal_headers(), json=payload)
            assert resp.status_code == 200
        final_job = store.get_job(job_id)
        assert final_job is not None
        assert final_job.status is PipelineStatus.COMPLETED
        assert final_job.history[-1]["status"] == PipelineStatus.COMPLETED.value


def test_pipeline_e2e_failure_marks_failed_and_dlq(monkeypatch, tmp_path):
    failing_launcher = _FailingWorkflowLauncher()
    app = _build_app_with_launcher(monkeypatch, failing_launcher)
    client = TestClient(app)
    dlq_calls: List[Dict[str, Any]] = []

    def _record_failure(**payload):
        dlq_calls.append(payload)

    monkeypatch.setattr(summariser_refactored, "publish_pipeline_failure", _record_failure)
    monkeypatch.setattr(summariser_refactored, "create_state_store_from_env", lambda: app.state.state_store)

    class _ExplodingSummariser:
        def __init__(self, *args, **kwargs):
            pass

        def summarise(self, *_args, **_kwargs):
            raise RuntimeError("summarise exploded")

    monkeypatch.setattr(summariser_refactored, "RefactoredSummariser", _ExplodingSummariser)

    ingest_resp = client.post("/ingest", json=_ingest_payload(suffix="-fail"))
    assert ingest_resp.status_code == 502
    assert ingest_resp.json()["detail"] == "Failed to dispatch workflow"
    assert failing_launcher.job_ids, "launcher should have been invoked before failing"
    job_id = failing_launcher.job_ids[-1]

    job = app.state.state_store.get_job(job_id)
    assert job is not None
    assert job.status is PipelineStatus.FAILED

    payload_path = tmp_path / "input.json"
    payload_path.write_text(json.dumps({"document": {"text": "Stub text for failure case"}}), encoding="utf-8")

    with pytest.raises(RuntimeError, match="summarise exploded"):
        summariser_refactored._cli(["--input", str(payload_path), "--dry-run", "--job-id", job_id])

    failed_job = app.state.state_store.get_job(job_id)
    assert failed_job is not None
    assert failed_job.status is PipelineStatus.FAILED
    assert failed_job.history[-1]["status"] == PipelineStatus.FAILED.value

    assert dlq_calls, "pipeline failure should publish to DLQ"
    last_call = dlq_calls[-1]
    assert last_call["job_id"] == job_id
    assert last_call["stage"] in {"SUMMARY_JOB", "SUPERVISOR"}
