from pathlib import Path

import pytest
from pypdf import PdfWriter

from src.config import AppConfig
from src.services import docai_helper
from src.services.docai_helper import OCRService

pytestmark = pytest.mark.integration


def _make_pdf(tmp_path: Path, pages: int) -> Path:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    path = tmp_path / f"integration_{pages}.pdf"
    with path.open("wb") as fh:
        writer.write(fh)
    return path


def test_large_pdf_triggers_local_split(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path, 450)

    decisions = []

    def record_decision(
        *, pages: int, decision: str, cfg, request_id: str, retry_on_page_limit: bool = False
    ) -> None:
        decisions.append(
            {
                "pages": pages,
                "decision": decision,
                "retry": retry_on_page_limit,
                "request_id": request_id,
            }
        )

    calls = {"count": 0}

    def fake_chunk(**_kwargs):
        calls["count"] += 1
        return {
            "text": f"chunk-{calls['count']}",
            "pages": [{"page_number": 1, "text": f"page-{calls['count']}"}],
        }

    monkeypatch.setattr(docai_helper, "_log_decision", record_decision)
    monkeypatch.setattr(docai_helper, "_process_chunk_with_docai", fake_chunk)

    cfg = AppConfig(
        project_id="proj",
        DOC_AI_OCR_PROCESSOR_ID="processor123",
        DOC_AI_SPLITTER_PROCESSOR_ID="",
    )
    service = OCRService(
        processor_id="processor123",
        config=cfg,
        doc_ai_splitter_id=None,
        force_split_min_pages=40,
        client_factory=lambda endpoint: object(),
    )

    result = service.process(str(pdf))

    assert calls["count"] >= 10
    assert result["pages"]
    assert decisions and decisions[0]["decision"] == "local_pypdf_split"
