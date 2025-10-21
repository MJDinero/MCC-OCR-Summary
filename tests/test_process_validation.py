import os
from fastapi.testclient import TestClient

from src.main import create_app
from src.errors import OCRServiceError, SummarizationError, DriveServiceError


def _setup_env():
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


def test_ingest_validates_object_payload():
    _setup_env()
    app = create_app()
    client = TestClient(app)
    resp = client.post("/ingest", json={"object": {"bucket": "b"}})
    assert resp.status_code == 400
    assert "required" in resp.json()["detail"].lower()


def test_internal_event_rejects_missing_token():
    _setup_env()
    app = create_app()
    client = TestClient(app)
    ingest = client.post(
        "/ingest",
        json={
            "object": {"bucket": "b", "name": "doc.pdf", "generation": "1"},
            "trace_id": "t",
        },
    )
    job_id = ingest.json()["job_id"]
    resp = client.post(
        f"/internal/jobs/{job_id}/events",
        json={"status": "OCR_DONE"},
    )
    assert resp.status_code == 401


def _configure_stubs(app):
    app.state.ocr_service.process = lambda payload, **kwargs: {
        "text": "A" * 400,
        "pages": [{"text": "A" * 200}],
    }

    async def _fake_summary(_: str) -> dict[str, str]:
        return {
            "Patient Information": "N/A",
            "Medical Summary": "Summary text " * 30,
            "Billing Highlights": "N/A",
            "Legal / Notes": "N/A",
        }

    app.state.summariser.summarise_async = _fake_summary  # type: ignore[attr-defined]
    app.state.drive_client.upload_pdf = lambda payload, folder_id=None, log_context=None: "drive-id"  # type: ignore[attr-defined]


def test_process_rejects_non_pdf_upload():
    _setup_env()
    app = create_app()
    _configure_stubs(app)
    client = TestClient(app)
    files = {"file": ("doc.txt", b"hello", "text/plain")}
    resp = client.post("/process", files=files)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "File must be a PDF"


def test_process_returns_502_on_docai_failure():
    _setup_env()
    app = create_app()
    _configure_stubs(app)
    def _raise_ocr(payload, **kwargs):
        raise OCRServiceError("boom")

    app.state.ocr_service.process = _raise_ocr  # type: ignore[attr-defined]
    client = TestClient(app)
    files = {"file": ("doc.pdf", b"%PDF-1.4\n", "application/pdf")}
    resp = client.post("/process", files=files)
    assert resp.status_code == 502
    assert resp.json()["detail"] == "Document AI processing failed"


def test_process_returns_502_on_summary_failure():
    _setup_env()
    app = create_app()
    _configure_stubs(app)

    async def _raise_summary(_: str):
        raise SummarizationError("bad summary")

    app.state.summariser.summarise_async = _raise_summary  # type: ignore[attr-defined]
    client = TestClient(app)
    files = {"file": ("doc.pdf", b"%PDF-1.4\n", "application/pdf")}
    resp = client.post("/process", files=files)
    assert resp.status_code == 502
    assert resp.json()["detail"] == "Summary generation failed"


def test_process_returns_502_on_drive_upload_failure():
    _setup_env()
    app = create_app()
    _configure_stubs(app)
    def _raise_drive_upload(payload, folder_id=None, log_context=None):
        raise DriveServiceError("drive")

    app.state.drive_client.upload_pdf = _raise_drive_upload  # type: ignore[attr-defined]
    client = TestClient(app)
    files = {"file": ("doc.pdf", b"%PDF-1.4\n", "application/pdf")}
    resp = client.post("/process", files=files)
    assert resp.status_code == 502
    assert resp.json()["detail"] == "Failed to upload PDF to Drive"


def test_process_drive_returns_502_on_download_failure():
    _setup_env()
    app = create_app()
    _configure_stubs(app)
    def _raise_drive_download(file_id, log_context=None):
        raise DriveServiceError("download")

    app.state.drive_client.download_pdf = _raise_drive_download  # type: ignore[attr-defined]
    client = TestClient(app)
    resp = client.get("/process_drive", params={"file_id": "doc"})
    assert resp.status_code == 502
    assert resp.json()["detail"] == "Failed to download file from Drive"
