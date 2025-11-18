from __future__ import annotations

import io

import pytest

from src.services.pdf_writer import PDFWriter, ReportLabBackend


def test_pdf_roundtrip_contains_canonical_headings() -> None:
    pytest.importorskip("reportlab")
    pypdf = pytest.importorskip("pypdf")

    writer = PDFWriter(ReportLabBackend())
    sections = [
        ("Provider Seen", "Facility context captured."),
        (
            "Reason for Visit",
            "- Primary concern addressed\n- Follow-up scheduled in two weeks",
        ),
        ("Clinical Findings", "- Imaging reviewed and unremarkable"),
        ("Treatment / Follow-up Plan", "- Continue physical therapy weekly"),
        ("Diagnoses", "- M54.5 Low back pain"),
        ("Healthcare Providers", "- Dr. Example Provider"),
        ("Medications / Prescriptions", "- Gabapentin 100 mg nightly"),
    ]

    pdf_bytes = writer.build("Document Summary", sections)
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)

    expected_headings = [
        "Provider Seen",
        "Reason for Visit",
        "Clinical Findings",
        "Treatment / Follow-up Plan",
        "Diagnoses",
        "Healthcare Providers",
        "Medications / Prescriptions",
    ]
    for heading in expected_headings:
        assert heading in extracted
    assert "Structured Indices" not in extracted
    assert "Summary Lists" not in extracted
