from __future__ import annotations

import io
from typing import Iterable, Tuple

import pytest
from pypdf import PdfReader

from src.services.pdf_writer import PDFWriter, ReportLabBackend

CANONICAL_HEADERS = [
    "Intro Overview",
    "Key Points",
    "Detailed Findings",
    "Care Plan & Follow-Up",
    "Diagnoses",
    "Providers",
    "Medications / Prescriptions",
]

FORBIDDEN_PHRASES = [
    "Structured Indices",
    "Summary Lists",
    "(Condensed)",
    "Document processed in",
]


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
        ("Intro Overview", "Patient presented for scheduled follow-up visit."),
        (
            "Key Points",
            "- Vitals remained stable throughout observation.\n"
            "- Follow-up with cardiology scheduled in four weeks.",
        ),
        (
            "Detailed Findings",
            "- Imaging studies were reviewed and demonstrated no acute findings.",
        ),
        (
            "Care Plan & Follow-Up",
            "- Continue home monitoring log.\n- Maintain medication adherence.",
        ),
        ("Diagnoses", "- I10 Primary hypertension\n- E78.5 Hyperlipidemia"),
        ("Providers", "- Dr. Emma Rivera"),
        ("Medications / Prescriptions", "- Lisinopril 20 mg daily\n- Atorvastatin 40 mg daily"),
    ]
    pdf_bytes, text, page_count = _render_pdf(sections)

    assert pdf_bytes.startswith(b"%PDF-")
    assert page_count >= 1
    for header in CANONICAL_HEADERS:
        assert header in text
    for marker in FORBIDDEN_PHRASES:
        assert marker not in text
    for bullet in ["- I10 Primary hypertension", "- Dr. Emma Rivera", "- Lisinopril 20 mg daily"]:
        assert bullet in text


def test_pdf_contract_long_content_spans_multiple_pages() -> None:
    long_detail = " ".join(["Detailed clinical observation."] * 600)
    sections = [
        ("Intro Overview", "Comprehensive intake spanning hundreds of pages."),
        (
            "Key Points",
            "- Encounter spans multiple specialties.\n- Coordination with oncology and cardiology teams.",
        ),
        ("Detailed Findings", long_detail),
        ("Care Plan & Follow-Up", "- Weekly lab panel.\n- Imaging every 6 weeks."),
        ("Diagnoses", "- C50.919 Malignant neoplasm of breast"),
        ("Providers", "- Multidisciplinary Team"),
        ("Medications / Prescriptions", "- Chemotherapy per protocol"),
    ]
    _bytes, _text, pages = _render_pdf(sections, title="Large Contract Summary")
    assert pages > 1
