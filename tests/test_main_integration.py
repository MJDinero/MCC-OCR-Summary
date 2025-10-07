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
        return {"text": "stub ocr text", "pages": [{"page_number": 1, "text": "stub ocr text"}]}


class StubSummariser(Summariser):  # type: ignore[misc]
    def __init__(self):
        pass

    def summarise(self, text: str) -> str:  # noqa: D401
        return f"summary({text})"


class StubPDF(PDFWriter):  # type: ignore[misc]
    def __init__(self):
        pass

    def build(self, summary: str) -> bytes:  # noqa: D401
        return PDF_BYTES


def test_health_and_process_stub():
    app = create_app()
    # Inject stubs
    app.state.ocr_service = StubOCR()
    app.state.summariser = StubSummariser()
    app.state.pdf_writer = StubPDF()

    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

    upload = {"file": ("doc.pdf", PDF_BYTES, "application/pdf")}
    resp = client.post("/process", files=upload)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/pdf")
    assert resp.content.startswith(b"%PDF-")
