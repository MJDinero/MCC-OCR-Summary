from __future__ import annotations

import io
from typing import Iterable, Tuple

from pypdf import PdfReader

from src.services.pdf_writer import PDFWriter, ReportLabBackend
from src.services.bible import (
    CANONICAL_SECTION_ORDER,
    FORBIDDEN_PDF_PHRASES,
)


def _render_pdf(
    sections: Iterable[Tuple[str, str]],
    title: str = "Contract Summary",
) -> tuple[bytes, str, int]:
    writer = PDFWriter(ReportLabBackend())
    pdf_bytes = writer.build(title, list(sections))
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = "\n".join(filter(None, (page.extract_text() for page in reader.pages)))
    return pdf_bytes, text, len(reader.pages)


def test_pdf_contract_has_canonical_sections_and_clean_lists() -> None:
    sections = [
        ("Provider Seen", "Patient presented for scheduled follow-up visit."),
        (
            "Reason for Visit",
            "- Vitals remained stable throughout observation.\n"
            "- Follow-up with cardiology scheduled in four weeks.",
        ),
        (
            "Clinical Findings",
            "- Imaging studies were reviewed and demonstrated no acute findings.",
        ),
        (
            "Treatment / Follow-up Plan",
            "- Continue home monitoring log.\n- Maintain medication adherence.",
        ),
        ("Diagnoses", "- I10 Primary hypertension\n- E78.5 Hyperlipidemia"),
        ("Healthcare Providers", "- Dr. Emma Rivera"),
        (
            "Medications / Prescriptions",
            "- Lisinopril 20 mg daily\n- Atorvastatin 40 mg daily",
        ),
    ]
    pdf_bytes, text, page_count = _render_pdf(sections)

    assert pdf_bytes.startswith(b"%PDF-")
    assert page_count >= 1
    for header in CANONICAL_SECTION_ORDER:
        assert header in text
        assert text.count(header) == 1
    lower_text = text.lower()
    for marker in FORBIDDEN_PDF_PHRASES:
        assert marker.lower() not in lower_text
    for bullet in [
        "- I10 Primary hypertension",
        "- Dr. Emma Rivera",
        "- Lisinopril 20 mg daily",
    ]:
        assert bullet in text


def test_pdf_contract_long_content_spans_multiple_pages() -> None:
    long_detail = " ".join(["Detailed clinical observation."] * 600)
    sections = [
        ("Provider Seen", "Comprehensive intake spanning hundreds of pages."),
        (
            "Reason for Visit",
            "- Encounter spans multiple specialties.\n- Coordination with oncology and cardiology teams.",
        ),
        ("Clinical Findings", long_detail),
        ("Treatment / Follow-up Plan", "- Weekly lab panel.\n- Imaging every 6 weeks."),
        ("Diagnoses", "- C50.919 Malignant neoplasm of breast"),
        ("Healthcare Providers", "- Multidisciplinary Team"),
        ("Medications / Prescriptions", "- Chemotherapy per protocol"),
    ]
    _bytes, _text, pages = _render_pdf(sections, title="Large Contract Summary")
    assert pages > 1
