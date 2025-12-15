from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.main import create_app
from src.models.summary_contract import SummaryContract, SummarySection

pytestmark = pytest.mark.integration


class StubLauncher:
    def launch(self, *, job, parameters=None, trace_context=None):
        return "exec/mock"


class StubSummariser:
    chunk_target_chars = 1200
    chunk_hard_max = 1800

    def _contract(self, text: str) -> dict[str, Any]:  # pragma: no cover - simple stub
        sections = [
            SummarySection(
                slug="medical_summary",
                title="Medical Summary",
                content="Refactored summary output " + ("-" * 520),
                ordinal=1,
                kind="narrative",
            )
        ]
        return SummaryContract(
            schema_version="test",
            sections=sections,
            claims=[],
            evidence_spans=[],
            metadata={"source": "test"},
            claims_notice="stub",
        ).to_dict()

    def summarise(self, text: str):  # pragma: no cover - simple stub
        return self._contract(text)

    async def summarise_async(self, text: str):  # pragma: no cover - reuse sync result
        return self._contract(text)


def test_health_and_ingest(monkeypatch):
    monkeypatch.setenv("PROJECT_ID", "proj")
    monkeypatch.setenv("REGION", "us")
    monkeypatch.setenv("DOC_AI_PROCESSOR_ID", "pid")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("DRIVE_INPUT_FOLDER_ID", "in")
    monkeypatch.setenv("DRIVE_REPORT_FOLDER_ID", "out")
    monkeypatch.setenv("PIPELINE_STATE_BACKEND", "memory")
    monkeypatch.setenv("SUMMARISER_JOB_NAME", "job-summary")
    monkeypatch.setenv("PDF_JOB_NAME", "job-pdf")
    monkeypatch.setenv("INTERNAL_EVENT_TOKEN", "token")

    app = create_app()
    app.state.workflow_launcher = StubLauncher()
    app.state.summariser = StubSummariser()
    client = TestClient(app)

    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

    payload = {
        "object": {"bucket": "b", "name": "doc.pdf", "generation": "1"},
        "trace_id": "trace",
    }
    resp = client.post("/ingest", json=payload)
    assert resp.status_code == 202
    body = resp.json()
    assert body["workflow_execution"] == "exec/mock"
    summary_dict = app.state.summariser.summarise("sample text")
    summary_text = SummaryContract.from_mapping(summary_dict).as_text()
    assert len(summary_text) >= 500
