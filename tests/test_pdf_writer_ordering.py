from io import BytesIO

from pypdf import PdfReader

from src.services.pdf_writer import PDFWriter, ReportLabBackend


def test_pdf_writer_section_order_and_extras():
    writer = PDFWriter(ReportLabBackend())
    sections = [
        ("Billing Highlights", "BILL"),
        ("Patient Information", "PAT"),
        ("Legal / Notes", "LEGAL"),
        ("Medical Summary", "MED"),
        ("Extra Section", "EXTRA"),
    ]
    data = writer.build("Canonical Order", sections)
    assert data.startswith(b"%PDF-")
    txt = "\n".join(
        filter(None, (page.extract_text() for page in PdfReader(BytesIO(data)).pages))
    )
    i_pat = txt.find("Patient Information")
    i_med = txt.find("Medical Summary")
    i_bill = txt.find("Billing Highlights")
    i_legal = txt.find("Legal / Notes")
    assert -1 not in {i_pat, i_med, i_bill, i_legal}
    assert i_pat < i_med < i_bill < i_legal
    i_extra = txt.find("Extra Section")
    assert i_extra > i_legal
