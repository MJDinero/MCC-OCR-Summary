from __future__ import annotations

from pathlib import Path

import pytest

from scripts import validate_summary as validator
from src.services.pdf_writer_refactored import PDFWriterRefactored
from src.services.summariser_refactored import RefactoredSummariser, ChunkSummaryBackend


class _MiniBackend(ChunkSummaryBackend):
    def summarise_chunk(self, *, chunk_text: str, chunk_index: int, total_chunks: int, estimated_tokens: int):
        return {
            "overview": "Follow-up visit for hypertension.",
            "key_points": ["Blood pressure improved"],
            "clinical_details": ["BP 128/82"],
            "care_plan": ["Continue medications"],
            "diagnoses": ["I10 Hypertension"],
            "providers": ["Dr Example"],
            "medications": ["Lisinopril 10 mg"],
        }


def _write_pdf(tmp_path: Path, payload: dict[str, object]) -> Path:
    writer = PDFWriterRefactored()
    pdf_bytes = writer.build(payload)
    pdf_path = tmp_path / "summary.pdf"
    pdf_path.write_bytes(pdf_bytes)
    return pdf_path


def test_summariser_pdf_validator_flow(tmp_path: Path) -> None:
    summariser = RefactoredSummariser(backend=_MiniBackend())
    contract_dict = summariser.summarise(
        "Patient evaluated for hypertension.",
        doc_metadata={"pages": [{"page_number": 1, "text": "Patient evaluated for hypertension."}]},
    )
    pdf_path = _write_pdf(tmp_path, contract_dict)
    result = validator.validate_pdf(
        pdf_path=pdf_path,
        expected_pages=1,
        required_headings=validator.DEFAULT_REQUIRED_HEADINGS,
    )
    assert result.is_success
    assert validator._validate_claims(contract_dict, strict=True)


def test_validator_warning_mode(tmp_path: Path) -> None:
    summariser = RefactoredSummariser(backend=_MiniBackend())
    payload = summariser.summarise(
        "Patient evaluated for hypertension.",
        doc_metadata={"pages": [{"page_number": 1, "text": "Patient evaluated for hypertension."}]},
    )
    payload["_claims"] = []
    payload["_claims_notice"] = "disabled"
    pdf_path = _write_pdf(tmp_path, payload)
    result = validator.validate_pdf(
        pdf_path=pdf_path,
        expected_pages=1,
        required_headings=validator.DEFAULT_REQUIRED_HEADINGS,
    )
    assert result.is_success
    assert validator._validate_claims(payload, strict=False) is False
    with pytest.raises(validator.ValidationError):
        validator._validate_claims(payload, strict=True)
