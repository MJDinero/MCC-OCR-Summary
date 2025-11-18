from __future__ import annotations

import json
from pathlib import Path

from src.services import summariser_refactored as ref_mod


def test_refactored_cli_dry_run(tmp_path) -> None:
    output_path = tmp_path / "summary.json"
    ref_mod._cli(
        [
            "--input",
            str(Path("tests/fixtures/sample_ocr.json")),
            "--output",
            str(output_path),
            "--dry-run",
        ]
    )
    assert output_path.exists()
    data = json.loads(output_path.read_text(encoding="utf-8"))
    medical_summary = data["Medical Summary"]
    assert len(medical_summary) >= 400
    assert "Reason for Visit:" in medical_summary
    assert "Clinical Findings:" in medical_summary
