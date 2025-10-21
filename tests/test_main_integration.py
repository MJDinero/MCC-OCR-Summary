from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from src.main import create_app


SAMPLE_PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\nstartxref\n0\n%%EOF"


def _setup_env(monkeypatch: Any) -> None:
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
    monkeypatch.setenv("STUB_MODE", "true")


def test_health_and_ingest(monkeypatch):
    _setup_env(monkeypatch)
    app = create_app()
    app.state.workflow_launcher = lambda **_: "exec/mock"  # type: ignore[assignment]
    client = TestClient(app)

    for path in ("/healthz", "/readyz", "/health", "/"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    payload = {
        "object": {"bucket": "b", "name": "doc.pdf", "generation": "1"},
        "trace_id": "trace",
    }
    resp = client.post("/ingest", json=payload)
    assert resp.status_code == 202
    body = resp.json()
    assert body["workflow_execution"] == "exec/mock"


def test_process_routes(monkeypatch):
    _setup_env(monkeypatch)
    app = create_app()

    async def _fake_summary(_: str) -> dict[str, str]:
        return {
            "Patient Information": "N/A",
            "Medical Summary": "Summary text" * 50,
            "Billing Highlights": "N/A",
            "Legal / Notes": "N/A",
        }

    app.state.ocr_service.process = lambda __, **kwargs: {  # type: ignore[attr-defined]
        "text": "A" * 400,
        "pages": [{"text": "A" * 200}],
    }
    app.state.summariser.summarise_async = _fake_summary  # type: ignore[attr-defined]
    app.state.drive_client.download_pdf = lambda file_id, log_context=None: SAMPLE_PDF if file_id == "drive-file" else SAMPLE_PDF  # type: ignore[attr-defined]
    app.state.drive_client.upload_pdf = lambda payload, folder_id=None, log_context=None: "uploaded-id"  # type: ignore[attr-defined]

    client = TestClient(app)

    files = {"file": ("doc.pdf", SAMPLE_PDF, "application/pdf")}
    resp = client.post("/process", files=files)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"

    drive_resp = client.get("/process_drive", params={"file_id": "drive-file"})
    assert drive_resp.status_code == 200
    payload = drive_resp.json()
    assert payload["report_file_id"] == "uploaded-id"
    assert payload["supervisor_passed"] in (True, False)
