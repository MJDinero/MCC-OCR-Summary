from pathlib import Path
from unittest.mock import patch

from PyPDF2 import PdfWriter

from src.services.docai_helper import OCRService


def _make_pdf(tmp_path: Path, pages: int) -> Path:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    p = tmp_path / f"integration_{pages}.pdf"
    with p.open("wb") as f:
        writer.write(f)
    return p


def test_batch_split_triggers_multiple_batches(tmp_path, monkeypatch):
    # Create 450 page PDF (should split into 3 parts of 200/200/50)
    pdf = _make_pdf(tmp_path, 450)

    # Fake batch result returned per part
    def fake_batch_process(input_uri, output_uri, processor_id, region, project_id=None, clients=None):
        # minimal shape used by integration code
        # fabricate pages based on unique counter to ensure merge distinctness
        return {
            "text": f"TEXT:{Path(input_uri).name}",
            "pages": [{"page_number": i + 1, "text": f"p{i+1}"} for i in range(5)],
        }

    class _DummyClient:
        def process_document(self, request):  # pragma: no cover - never called in batch path
            return {"document": {"text": "", "pages": []}}

    svc = OCRService(processor_id="processor123", client_factory=lambda endpoint: _DummyClient())
    with patch(
        "src.services.docai_helper.batch_process_documents_gcs",
        side_effect=fake_batch_process,
    ) as mock_batch:
        class _SplitResult:
            def __init__(self):
                self.parts = [
                    "gs://intake/splits/x/part_0001.pdf",
                    "gs://intake/splits/x/part_0002.pdf",
                    "gs://intake/splits/x/part_0003.pdf",
                ]
                self.manifest_gcs_uri = "gs://intake/splits/x/manifest.json"

        with patch(
            "src.services.docai_helper.split_pdf_by_page_limit", return_value=_SplitResult()
        ) as mock_split:
            result = svc.process(str(pdf))

    assert mock_batch.call_count == 3
    assert len(result["pages"]) == 15  # 3 * 5 from fake result pages
    assert result["batch_metadata"]["parts"] == 3
