from src.services.pdf_writer import PDFWriter, MinimalPDFBackend


def test_pdf_writer_structured_sections():
    writer = PDFWriter(MinimalPDFBackend())
    data = writer.build(
        {
            "Patient Information": "Jane Doe",
            "Medical Summary": "Findings normal",
            "Billing Highlights": "Code X",
            "Legal / Notes": "N/A",
        }
    )
    assert data.startswith(b"%PDF-")
