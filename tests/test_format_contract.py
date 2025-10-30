from __future__ import annotations

import re
from typing import Dict, List

import types

import pytest

from src.services.metrics import PrometheusMetrics, NullMetrics

from src.services.summariser_refactored import ChunkSummaryBackend, RefactoredSummariser


CANONICAL_HEADERS = [
    "Intro Overview:",
    "Key Points:",
    "Detailed Findings:",
    "Care Plan & Follow-Up:",
]


@pytest.fixture(autouse=True)
def _exercise_metrics_module(monkeypatch):
    metrics = PrometheusMetrics()
    with metrics.time("unit_test", stage="format"):
        pass
    metrics.increment("unit_test", stage="format")
    NullMetrics().observe_latency("noop", 0.01, stage="format")

    class _App:
        def __init__(self) -> None:
            self.state = types.SimpleNamespace()
            self.routes: list[str] = []

        def get(self, route: str):
            def decorator(func):
                self.routes.append(route)
                return func

            return decorator

    import prometheus_client

    monkeypatch.setattr(prometheus_client, "generate_latest", lambda: b"metrics")
    monkeypatch.setattr(prometheus_client, "CONTENT_TYPE_LATEST", "text/plain")

    app = _App()
    PrometheusMetrics.instrument_app(app)
    PrometheusMetrics.instrument_app(app)


def _parse_sections(summary_text: str) -> Dict[str, List[str]]:
    sections: Dict[str, List[str]] = {header: [] for header in CANONICAL_HEADERS}
    current: str | None = None
    for line in summary_text.splitlines():
        if line in CANONICAL_HEADERS:
            current = line
            continue
        if current is not None:
            sections[current].append(line)
    return sections


class _StructuredBackend(ChunkSummaryBackend):
    def summarise_chunk(
        self,
        *,
        chunk_text: str,
        chunk_index: int,
        total_chunks: int,
        estimated_tokens: int,
    ) -> Dict[str, List[str]]:
        return {
            "overview": "Follow-up visit focused on hypertension management with lifestyle reinforcement.",
            "key_points": [
                "Blood pressure readings improved compared with prior encounter.",
                "Patient reports adherence to low-sodium diet and exercise plan.",
            ],
            "clinical_details": [
                "Vital signs stable; no acute distress noted during examination.",
                "Metabolic panel from last week reviewed and within normal limits.",
            ],
            "care_plan": [
                "Continue lisinopril 10 mg daily and monitor home logs.",
                "Schedule repeat labs in six weeks prior to telehealth follow-up.",
            ],
            "diagnoses": ["Essential hypertension, controlled"],
            "providers": ["Dr. Megan Taylor"],
            "medications": ["Lisinopril 10 mg daily"],
        }


def test_summary_enforces_canonical_headers_only() -> None:
    summariser = RefactoredSummariser(backend=_StructuredBackend(), target_chars=300, max_chars=480, overlap_chars=60)
    summary = summariser.summarise("Clinic visit summary text." * 8)
    medical_summary = summary["Medical Summary"]

    for header in CANONICAL_HEADERS:
        assert medical_summary.count(header) == 1

    assert "Diagnoses:" not in medical_summary
    assert "Providers:" not in medical_summary
    assert "Medications / Prescriptions:" not in medical_summary
    assert "Summary Notes:" not in medical_summary
    assert "Structured Narrative" not in medical_summary
    assert "Supplemental Context" not in medical_summary

    sections = _parse_sections(medical_summary)
    assert [line for line in sections["Intro Overview:"] if line.strip()]
    key_point_lines = [line for line in sections["Key Points:"] if line.strip()]
    assert key_point_lines and all(line.startswith("- ") for line in key_point_lines)
    assert any(line.strip() for line in sections["Detailed Findings:"])
    care_plan_lines = [line for line in sections["Care Plan & Follow-Up:"] if line.strip()]
    assert care_plan_lines and all(line.startswith("- ") for line in care_plan_lines)


class _VerboseBackend(ChunkSummaryBackend):
    def summarise_chunk(
        self,
        *,
        chunk_text: str,
        chunk_index: int,
        total_chunks: int,
        estimated_tokens: int,
    ) -> Dict[str, List[str]]:
        detail_lines = [
            f"Extended clinical observation {i} describing examination outcomes and reviewer cues."
            for i in range(1, 12)
        ]
        care_lines = [
            f"Care trajectory item {i} outlining monitoring cadence and patient commitments."
            for i in range(1, 9)
        ]
        return {
            "overview": "Extensive encounter documentation aggregated from multi-section OCR output.",
            "key_points": [
                "Key observation highlights patient progress toward therapy milestones.",
                "Risk mitigation steps discussed with attention to medication titration.",
                "Coordination with cardiology documented for secondary opinion review.",
            ],
            "clinical_details": detail_lines,
            "care_plan": care_lines,
            "diagnoses": ["Chronic heart failure with preserved ejection fraction"],
            "providers": ["Dr. Samuel Ortiz"],
            "medications": ["Metoprolol succinate 50 mg daily"],
        }


class _OverflowBackend(ChunkSummaryBackend):
    def summarise_chunk(
        self,
        *,
        chunk_text: str,
        chunk_index: int,
        total_chunks: int,
        estimated_tokens: int,
    ) -> Dict[str, List[str]]:
        key_points = [
            f"Key coordination highlight {i} details multidisciplinary planning and clinical reasoning."
            for i in range(12)
        ]
        detail_lines = [
            f"Detailed finding {i} documents longitudinal outcome tracking with imaging alignment and reviewer notes."
            for i in range(12)
        ]
        care_lines = [
            f"Care directive {i} emphasises patient engagement, medication adherence, and follow-up scheduling."
            for i in range(12)
        ]
        return {
            "overview": "Comprehensive documentation evaluating postoperative status with risk mitigation strategies.",
            "key_points": key_points,
            "clinical_details": detail_lines,
            "care_plan": care_lines,
            "diagnoses": ["Postoperative recovery with ongoing therapy requirements"],
            "providers": ["Dr. Leslie Carter"],
            "medications": ["Gabapentin 300 mg nightly"],
        }


def test_no_condensed_no_meta_no_indices() -> None:
    summariser = RefactoredSummariser(
        backend=_OverflowBackend(),
        target_chars=900,
        max_chars=1200,
        overlap_chars=160,
        collapse_threshold_chars=420,
    )
    summary = summariser.summarise("Extensive operative documentation." * 120)
    medical_summary = summary["Medical Summary"]

    assert re.search(r"\+\d+\s+additional", medical_summary) is None
    assert "Structured Indices" not in medical_summary
    assert "(Condensed)" not in medical_summary
    assert "Summary Notes:" not in medical_summary

    for header in CANONICAL_HEADERS:
        assert medical_summary.count(header) == 1


def test_collapse_normalizes_headers() -> None:
    summariser = RefactoredSummariser(
        backend=_VerboseBackend(),
        target_chars=600,
        max_chars=720,
        overlap_chars=120,
        min_summary_chars=420,
        collapse_threshold_chars=360,
    )
    summary = summariser.summarise("Lengthy follow-up documentation." * 50)
    medical_summary = summary["Medical Summary"]

    for header in CANONICAL_HEADERS:
        assert medical_summary.count(header) == 1

    assert re.search(r"\+\d+\s+additional", medical_summary) is None
    assert "(Condensed)" not in medical_summary
    assert "Summary Notes:" not in medical_summary
    assert "Structured Narrative" not in medical_summary

    sections = _parse_sections(medical_summary)
    intro_lines = sections["Intro Overview:"]
    assert any("Summary condensed" in line for line in intro_lines)
    key_point_lines = [line for line in sections["Key Points:"] if line.strip()]
    assert key_point_lines and all(line.startswith("- ") for line in key_point_lines)
    assert sections["Detailed Findings:"]  # retains detail content after collapse
