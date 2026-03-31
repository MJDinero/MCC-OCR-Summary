from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "benchmark_summary_strategies.py"


def test_benchmark_summary_strategies_json_output() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert len(payload) == 4
    assert {entry["case"] for entry in payload} == {
        "small_clean_chunked",
        "small_clean_one_shot",
        "medium_clean_one_shot",
        "large_noisy_auto",
    }
    assert all(entry["summary_valid"] for entry in payload)
    assert all(entry["pdf_valid"] for entry in payload)
