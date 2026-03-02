import pytest

from src.services.pipeline import (
    InMemoryStateStore,
    NoopWorkflowLauncher,
    create_state_store_from_env,
    create_workflow_launcher_from_env,
)


def _set_non_local_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("K_SERVICE", "mcc-ocr-summary")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("UNIT_TESTING", raising=False)


def test_non_local_runtime_rejects_memory_state_backend(monkeypatch: pytest.MonkeyPatch):
    _set_non_local_runtime(monkeypatch)
    monkeypatch.delenv("PIPELINE_STATE_BACKEND", raising=False)

    with pytest.raises(
        RuntimeError, match="PIPELINE_STATE_BACKEND=memory is only allowed"
    ):
        create_state_store_from_env()


def test_non_local_runtime_requires_workflow_name(monkeypatch: pytest.MonkeyPatch):
    _set_non_local_runtime(monkeypatch)
    monkeypatch.delenv("PIPELINE_WORKFLOW_NAME", raising=False)

    with pytest.raises(
        RuntimeError, match="PIPELINE_WORKFLOW_NAME must be configured"
    ):
        create_workflow_launcher_from_env()


def test_local_runtime_allows_memory_and_noop(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("PIPELINE_WORKFLOW_NAME", raising=False)
    monkeypatch.setenv("PIPELINE_STATE_BACKEND", "memory")

    assert isinstance(create_state_store_from_env(), InMemoryStateStore)
    assert isinstance(create_workflow_launcher_from_env(), NoopWorkflowLauncher)
