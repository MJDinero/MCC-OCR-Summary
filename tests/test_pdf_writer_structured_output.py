from io import BytesIO

from pypdf import PdfReader

from src.services.pdf_writer import PDFWriter, ReportLabBackend


def test_pdf_writer_renders_structured_indices():
    writer = PDFWriter(ReportLabBackend())
    sections = [
        ("Provider Seen", "John Doe intake."),
        ("Reason for Visit", "- Primary diagnosis discussed."),
        ("Clinical Findings", "Imaging negative."),
        ("Treatment / Follow-up Plan", "- Return in one month."),
        ("Diagnoses", "- Dx1\n- Dx2"),
        ("Healthcare Providers", "- Dr A"),
        ("Medications / Prescriptions", "- MedA\n- MedB"),
    ]
    pdf_bytes = writer.build("Structured Output", sections)
    assert pdf_bytes.startswith(b"%PDF-")
    reader = PdfReader(BytesIO(pdf_bytes))
    text = "\n".join(filter(None, (page.extract_text() for page in reader.pages)))
    for heading in [
        "Provider Seen",
        "Reason for Visit",
        "Clinical Findings",
        "Treatment / Follow-up Plan",
        "Diagnoses",
        "Healthcare Providers",
        "Medications / Prescriptions",
    ]:
        assert heading in text
    for line in ["- Dx1", "- Dx2", "- Dr A", "- MedA", "- MedB"]:
        assert line in text
    forbidden = [
        "Structured Indices",
        "Summary Lists",
        "(Condensed)",
        "Document processed in",
    ]
    for marker in forbidden:
        assert marker not in text
