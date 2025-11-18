from io import BytesIO

from pypdf import PdfReader

from src.services.pdf_writer import PDFWriter, ReportLabBackend


def test_pdf_writer_section_order_and_extras():
    writer = PDFWriter(ReportLabBackend())
    sections = [
        ("Clinical Findings", "DETAIL"),
        ("Medications / Prescriptions", "MEDS"),
        ("Provider Seen", "INTRO"),
        ("Healthcare Providers", "PROVIDERS"),
        ("Reason for Visit", "KEY"),
        ("Diagnoses", "DX"),
        ("Treatment / Follow-up Plan", "PLAN"),
        ("Extra Section", "EXTRA"),
    ]
    data = writer.build("Canonical Order", sections)
    assert data.startswith(b"%PDF-")
    txt = "\n".join(
        filter(None, (page.extract_text() for page in PdfReader(BytesIO(data)).pages))
    )
    order = [
        txt.find("Provider Seen"),
        txt.find("Reason for Visit"),
        txt.find("Clinical Findings"),
        txt.find("Treatment / Follow-up Plan"),
        txt.find("Diagnoses"),
        txt.find("Healthcare Providers"),
        txt.find("Medications / Prescriptions"),
    ]
    assert all(idx >= 0 for idx in order)
    assert order == sorted(order)
    i_extra = txt.find("Extra Section")
    assert i_extra > order[-1]
