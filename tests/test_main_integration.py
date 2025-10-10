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
        phrase = "Hypertension management plan follow up for diabetes with insulin glargine during routine visit. "
        text = phrase * 3 + "Dr. Smith coordinates hypertension management plan follow up for diabetes."
        return {"text": text, "pages": [{"page_number": 1, "text": text}]}


class StubSummariser(Summariser):  # type: ignore[misc]
    def __init__(self):
        pass

    def summarise(self, text: str) -> dict[str, str]:  # noqa: D401
        sentence = "Hypertension management plan follow up for diabetes with insulin glargine during routine visit."
        repeated = f"{sentence} {sentence} {sentence}"
        body = (
            "Provider Seen:\nDr. Smith\n\n"
            f"Reason for Visit:\n{repeated}\n\n"
            f"Clinical Findings:\n{repeated}\n\n"
            f"Treatment / Follow-Up Plan:\n{repeated}\n\n"
            f"Diagnoses:\n- {sentence}\n"
            "Providers:\n- Dr. Smith\n"
            "Medications / Prescriptions:\n- Insulin glargine"
        )
        return {
            'Patient Information': 'Stub patient',
            'Medical Summary': body,
            'Billing Highlights': 'N/A',
            'Legal / Notes': 'N/A',
            '_diagnoses_list': 'Type 2 diabetes',
            '_providers_list': 'Dr. Smith',
            '_medications_list': 'Insulin glargine',
        }


class StubPDF(PDFWriter):  # type: ignore[misc]
    def __init__(self):
        pass

    def build(self, summary: dict[str, str]) -> bytes:  # noqa: D401
        return PDF_BYTES


def _patched_app(monkeypatch):
    monkeypatch.setattr('src.main.OCRService', lambda *args, **kwargs: StubOCR())
    return create_app()


def test_health_and_process_stub(monkeypatch):
    app = _patched_app(monkeypatch)
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
