from io import BytesIO

from pypdf import PdfReader

from src.services.pdf_writer import PDFWriter, ReportLabBackend


def test_pdf_writer_renders_structured_indices():
    writer = PDFWriter(ReportLabBackend())
    sections = [
        ("Intro Overview", "John Doe intake."),
        ("Key Points", "- Primary diagnosis discussed."),
        ("Detailed Findings", "Imaging negative."),
        ("Care Plan & Follow-Up", "- Return in one month."),
        ("Diagnoses", "- Dx1\n- Dx2"),
        ("Providers", "- Dr A"),
        ("Medications / Prescriptions", "- MedA\n- MedB"),
    ]
    pdf_bytes = writer.build("Structured Output", sections)
    assert pdf_bytes.startswith(b"%PDF-")
    reader = PdfReader(BytesIO(pdf_bytes))
    text = "\n".join(filter(None, (page.extract_text() for page in reader.pages)))
    for heading in [
        "Intro Overview",
        "Key Points",
        "Detailed Findings",
        "Care Plan & Follow-Up",
        "Diagnoses",
        "Providers",
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
