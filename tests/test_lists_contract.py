from __future__ import annotations

from typing import Dict, List

from src.services.summariser_refactored import ChunkSummaryBackend, RefactoredSummariser


CANONICAL_HEADERS = [
    "Intro Overview:",
    "Key Points:",
    "Detailed Findings:",
    "Care Plan & Follow-Up:",
]


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


class _DuplicateBackend(ChunkSummaryBackend):
    def summarise_chunk(
        self,
        *,
        chunk_text: str,
        chunk_index: int,
        total_chunks: int,
        estimated_tokens: int,
    ) -> Dict[str, List[str]]:
        duplicate_line = "Continue physical therapy twice weekly for lumbar stabilization."
        imaging_line = "Lumbar MRI performed in August 2024 shows no recurrent herniation."
        return {
            "overview": "Ongoing rehabilitation visit documenting lumbar pain management progress.",
            "key_points": [duplicate_line, duplicate_line],
            "clinical_details": [duplicate_line, imaging_line, imaging_line],
            "care_plan": [duplicate_line],
            "diagnoses": ["Chronic lumbar strain"],
            "providers": ["Dr. Hana Patel"],
            "medications": ["Gabapentin 300 mg nightly"],
        }


def test_final_list_filters_and_dedupe() -> None:
    summariser = RefactoredSummariser(backend=_DuplicateBackend(), target_chars=280, max_chars=360, overlap_chars=40)
    summary = summariser.summarise("Rehab visit narrative." * 12)
    medical_summary = summary["Medical Summary"]

    sections = _parse_sections(medical_summary)

    # All bullet lines should be unique across sections after normalisation.
    bullet_lines: List[str] = []
    for header in CANONICAL_HEADERS[1:]:  # skip Intro Overview
        lines = sections[header]
        for line in lines:
            if line.startswith("- "):
                bullet_lines.append(line)

    normalised = [line.lstrip("-•").strip().lower() for line in bullet_lines]
    assert len(normalised) == len(set(normalised)), "Bullet lines should be unique across sections"

    # Imaging findings heading should appear once with bullets following.
    detail_lines = sections["Detailed Findings:"]
    assert detail_lines[0] == "Imaging Findings:"
    assert detail_lines[1].startswith("- Lumbar MRI performed")

    # Care plan section should retain at least one bullet line.
    care_plan_lines = [line for line in sections["Care Plan & Follow-Up:"] if line.startswith("- ")]
    assert care_plan_lines, "Care plan section should include bullet list"

    # Intro section should not contain bullet markers.
    assert all(not line.startswith("- ") for line in sections["Intro Overview:"])
