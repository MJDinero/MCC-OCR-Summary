import json
from types import SimpleNamespace

import src.services.pipeline as pipeline_module
from src.services.pipeline import CloudWorkflowsLauncher, InMemoryStateStore, PipelineJobCreate


class _StubExecutionsClient:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def create_execution(self, request, **kwargs):
        self.requests.append({"request": request, "kwargs": kwargs})
        return SimpleNamespace(name="executions/test")


class _Execution:
    def __init__(self, *, argument: str) -> None:
        self.argument = argument


class _CreateExecutionRequest:
    def __init__(self, *, parent: str, execution: _Execution) -> None:
        self.parent = parent
        self.execution = execution


def test_cloud_workflows_launcher_preserves_job_metadata_for_execution_matching(
    monkeypatch,
):
    monkeypatch.setattr(pipeline_module, "workflows", object())
    monkeypatch.setattr(
        pipeline_module,
        "executions_v1",
        SimpleNamespace(
            Execution=_Execution,
            CreateExecutionRequest=_CreateExecutionRequest,
        ),
    )
    monkeypatch.setattr(pipeline_module, "Retry", None)

    store = InMemoryStateStore()
    job = store.create_job(
        PipelineJobCreate(
            bucket="mcc-intake",
            object_name="uploads/drive/drive-input-123.pdf",
            generation="1",
            md5_hash="hash==",
            trace_id="trace-123",
            request_id="request-123",
            source="drive-poll",
            drive_file_id="drive-input-123",
        )
    )
    client = _StubExecutionsClient()
    launcher = CloudWorkflowsLauncher(
        workflow_name="projects/proj/locations/us-central1/workflows/docai-pipeline",
        client=client,
    )

    execution_name = launcher.launch(
        job=job,
        parameters={"pipeline_service_base_url": "https://pipeline.test"},
        trace_context="trace-123/1;o=1",
    )

    assert execution_name == "executions/test"
    assert client.requests
    payload = json.loads(client.requests[0]["request"].execution.argument)
    assert payload["job_id"] == job.job_id
    assert payload["metadata"]["drive_file_id"] == "drive-input-123"
    assert payload["metadata"]["source"] == "drive-poll"
    assert payload["pipeline_service_base_url"] == "https://pipeline.test"
