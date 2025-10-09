from src.services.pdf_writer import PDFWriter, MinimalPDFBackend


def test_pdf_writer_renders_structured_indices():
    writer = PDFWriter(MinimalPDFBackend())
    summary = {
        'Patient Information': 'John Doe',
        'Medical Summary': 'Base narrative',
        'Billing Highlights': 'N/A',
        'Legal / Notes': 'N/A',
        '_diagnoses_list': 'Dx1\nDx2',
        '_providers_list': 'Dr A',
        '_medications_list': 'MedA\nMedB',
    }
    pdf_bytes = writer.build(summary)
    assert pdf_bytes.startswith(b'%PDF-')
    text = pdf_bytes.decode('utf-8', errors='ignore')
    assert 'Structured Indices' in text
    assert 'Dx1' in text and 'MedB' in text