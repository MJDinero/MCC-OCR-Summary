import os
from fastapi.testclient import TestClient

from src.main import create_app
from src.services.pipeline import PipelineStatus


class FailingWorkflowLauncher:
    def launch(self, *, job, parameters=None, trace_context=None):
        raise RuntimeError("workflow offline")


def _env():
    os.environ["PROJECT_ID"] = "proj"
    os.environ["REGION"] = "us"
    os.environ["DOC_AI_PROCESSOR_ID"] = "pid"
    os.environ["OPENAI_API_KEY"] = "key"
    os.environ["DRIVE_INPUT_FOLDER_ID"] = "in"
    os.environ["DRIVE_REPORT_FOLDER_ID"] = "out"
    os.environ["PIPELINE_STATE_BACKEND"] = "memory"
    os.environ["SUMMARISER_JOB_NAME"] = "job-summary"
    os.environ["PDF_JOB_NAME"] = "job-pdf"
    os.environ["INTERNAL_EVENT_TOKEN"] = "token"


def test_ingest_marks_job_failed_when_workflow_launch_errors():
    _env()
    app = create_app()
    app.state.workflow_launcher = FailingWorkflowLauncher()
    client = TestClient(app)
    payload = {
        "object": {"bucket": "b", "name": "doc.pdf", "generation": "1"},
        "trace_id": "tid",
    }
    resp = client.post("/ingest", json=payload)
    assert resp.status_code == 502
    job_id = app.state.state_store.get_by_dedupe("b/doc.pdf@1").job_id  # type: ignore
    job = app.state.state_store.get_job(job_id)
    assert job.status is PipelineStatus.FAILED
    assert job.last_error["stage"] == "workflow_dispatch"
