from src.services.pdf_writer import PDFWriter, MinimalPDFBackend


def test_pdf_writer_section_order_and_extras():
    writer = PDFWriter(MinimalPDFBackend())
    data = writer.build({
        'Billing Highlights': 'BILL',  # out-of-order on purpose
        'Patient Information': 'PAT',
        'Legal / Notes': 'LEGAL',
        'Medical Summary': 'MED',
        'Extra Section': 'EXTRA'
    })
    assert data.startswith(b'%PDF-')
    # Extract text portion after header for ordering assertions
    txt = data.decode('utf-8', errors='ignore')
    # Ensure canonical order appears as indices increasing
    i_pat = txt.find('Patient Information')
    i_med = txt.find('Medical Summary')
    i_bill = txt.find('Billing Highlights')
    i_legal = txt.find('Legal / Notes')
    assert -1 not in {i_pat, i_med, i_bill, i_legal}
    assert i_pat < i_med < i_bill < i_legal
    # Extra section appears after canonical ones
    i_extra = txt.find('Extra Section')
    assert i_extra > i_legal
