from io import BytesIO

from pypdf import PdfReader

from src.services.pdf_writer import PDFWriter, ReportLabBackend


def test_pdf_writer_structured_sections():
    writer = PDFWriter(ReportLabBackend())
    sections = [
        ("Provider Seen", "Jane Doe, 54, presented for routine visit."),
        ("Reason for Visit", "- Stable examination today."),
        ("Clinical Findings", "No acute issues identified."),
        ("Treatment / Follow-up Plan", "- Return in 3 months."),
    ]
    data = writer.build("Encounter Summary", sections)
    assert data.startswith(b"%PDF-")
    text = "\n".join(
        filter(None, (page.extract_text() for page in PdfReader(BytesIO(data)).pages))
    )
    assert "Encounter Summary" in text
    assert "- Return in 3 months." in text
