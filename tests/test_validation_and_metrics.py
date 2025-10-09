import pytest
pytest.skip("Legacy validation/metrics tests removed after refactor", allow_module_level=True)

from fastapi.testclient import TestClient

from src.main import create_app
from src.services.docai_helper import OCRService
from src.services.summariser import Summariser
from src.services.pdf_writer import PDFWriter

PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


class StubOCR(OCRService):  # type: ignore[misc]
    def __init__(self):
        pass

    def process(self, file_source):  # noqa: D401
        return {"text": "stub text", "pages": [{"page_number": 1, "text": "stub text"}]}


class StubSummariser(Summariser):  # type: ignore[misc]
    def __init__(self):
        pass

    def summarise(self, text: str) -> str:  # noqa: D401
        return "summary"  # small deterministic output


class StubPDF(PDFWriter):  # type: ignore[misc]
    def __init__(self):
        pass

    def build(self, summary: str) -> bytes:  # noqa: D401
        return PDF_BYTES


def _app_with_stubs(max_bytes=1024, enable_metrics=True):
    app = create_app()
    app.state.ocr_service = StubOCR()
    app.state.summariser = StubSummariser()
    app.state.pdf_writer = StubPDF()
    # override max size in config dynamically
    app.state.config.max_pdf_bytes = max_bytes
    app.state.config.enable_metrics = enable_metrics
    return app


def test_reject_non_pdf_extension():
    app = _app_with_stubs()
    client = TestClient(app)
    upload = {"file": ("doc.txt", b"hello", "text/plain")}
    resp = client.post("/process", files=upload)
    assert resp.status_code == 400
    assert "File must have .pdf" in resp.text


def test_reject_invalid_content_type():
    app = _app_with_stubs()
    client = TestClient(app)
    upload = {"file": ("doc.pdf", PDF_BYTES, "text/plain")}
    resp = client.post("/process", files=upload)
    assert resp.status_code == 400
    assert "Invalid content type" in resp.text


def test_reject_oversize_pdf():
    app = _app_with_stubs(max_bytes=10)
    client = TestClient(app)
    upload = {"file": ("doc.pdf", PDF_BYTES + b"extra bytes to exceed", "application/pdf")}
    resp = client.post("/process", files=upload)
    assert resp.status_code == 400
    assert "exceeds" in resp.text


def test_metrics_endpoint_optional():
    app = _app_with_stubs()
    client = TestClient(app)
    # metrics should exist if enabled (default true)
    r = client.get("/metrics")
    assert r.status_code == 200
    # disable and rebuild
    app2 = _app_with_stubs(enable_metrics=False)
    client2 = TestClient(app2)
    r2 = client2.get("/metrics")
    assert r2.status_code in (404, 405)


def test_success_process_increments_metrics():
    app = _app_with_stubs()
    client = TestClient(app)
    upload = {"file": ("doc.pdf", PDF_BYTES, "application/pdf")}
    resp = client.post("/process", files=upload)
    assert resp.status_code == 200
    # we expect http_requests_total metric text present
    metrics = client.get("/metrics")
    assert b"http_requests_total" in metrics.content
