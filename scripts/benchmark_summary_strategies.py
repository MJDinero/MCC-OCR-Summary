#!/usr/bin/env python3
# ruff: noqa: E402
"""Offline benchmark harness for one-shot-first summary routing."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.models.summary_contract import SummaryContract
from src.services.pdf_writer_refactored import PDFWriterRefactored
from src.services.summariser_refactored import (
    AdaptiveSummariser,
    HeuristicChunkBackend,
    HeuristicOneShotBackend,
    RefactoredSummariser,
    SlidingWindowChunker,
    _prepare_summary_input,
)
from src.services.supervisor import CommonSenseSupervisor
SAMPLE_FIXTURE = ROOT / "tests" / "fixtures" / "sample_ocr.json"


def _load_sample_fixture() -> Dict[str, Any]:
    return json.loads(SAMPLE_FIXTURE.read_text(encoding="utf-8"))


def _build_medium_payload() -> Dict[str, Any]:
    sample = _load_sample_fixture()
    text = str(sample["text"])
    pages = [{"page_number": idx + 1, "text": text} for idx in range(1, 7)]
    return {
        "text": "\n\n".join(text for _ in range(6)),
        "metadata": {
            "facility": "Metro Care Clinic",
            "patient_info": "Synthetic medium fixture",
            "billing": "Synthetic benchmark only",
            "legal_notes": "Synthetic benchmark only",
            "pages": pages,
        },
    }


def _build_large_noisy_payload() -> Dict[str, Any]:
    base = (
        "Reason for Visit: Follow-up for lumbar pain after a lifting injury. "
        "Clinical Findings: Reduced lumbar range of motion, intact strength, and mild paraspinal tenderness. "
        "Plan: Continue physical therapy, ibuprofen 400 mg as needed, and follow up in six weeks. "
        "Provider Seen: Dr. Provider1. "
    )
    noise = (
        "Patient Name: Synthetic Patient. "
        "Medical Record Number: SYN-001. "
        "I understand that the following procedure carries risks and hazards. "
        "Call 911 if symptoms worsen. "
    )
    page_texts: List[Dict[str, Any]] = []
    blocks: List[str] = []
    for page_number in range(1, 31):
        page_block = ((base * 12) + (noise * 4)).strip()
        page_texts.append({"page_number": page_number, "text": page_block})
        blocks.append(page_block)
    return {
        "text": "\n\n".join(blocks),
        "metadata": {
            "facility": "Riverside Clinic",
            "patient_info": "Synthetic large fixture",
            "billing": "Synthetic benchmark only",
            "legal_notes": "Synthetic benchmark only",
            "pages": page_texts,
        },
    }


def _estimate_chunked_input_tokens(text: str, *, target_chars: int, max_chars: int, overlap_chars: int) -> int:
    chunker = SlidingWindowChunker(
        target_chars=target_chars,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
    )
    return sum(chunk.approx_tokens for chunk in chunker.split(_prepare_summary_input(text)))


def _benchmark_case(
    *,
    name: str,
    payload: Dict[str, Any],
    requested_strategy: str,
    one_shot_token_threshold: int,
) -> Dict[str, Any]:
    text = str(payload["text"])
    metadata = dict(payload.get("metadata") or {})
    chunked = RefactoredSummariser(
        backend=HeuristicChunkBackend(),
        target_chars=2400,
        max_chars=10000,
        overlap_chars=320,
        min_summary_chars=480,
    )
    summariser = AdaptiveSummariser(
        chunked_summariser=chunked,
        one_shot_backend=HeuristicOneShotBackend(),
        requested_strategy=requested_strategy,
        one_shot_token_threshold=one_shot_token_threshold,
        one_shot_max_pages=80,
        ocr_noise_ratio_threshold=0.18,
    )
    started = time.perf_counter()
    result = summariser.summarise_with_details(text, doc_metadata=metadata)
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    contract = SummaryContract.from_mapping(result.summary)
    pdf_bytes = PDFWriterRefactored().build(result.summary)
    supervisor = CommonSenseSupervisor()
    validation = supervisor.validate(
        ocr_text=text,
        summary=result.summary,
        doc_stats=supervisor.collect_doc_stats(
            text=text,
            pages=metadata.get("pages") or [],
            file_bytes=None,
        ),
    )
    route_metrics = result.route.metrics
    chunk_count = int(result.summary["metadata"].get("chunk_count") or 1)
    estimated_input_tokens = (
        route_metrics.estimated_tokens
        if result.final_strategy == "one_shot"
        else _estimate_chunked_input_tokens(
            text,
            target_chars=chunked.target_chars,
            max_chars=chunked.max_chars,
            overlap_chars=chunked.overlap_chars,
        )
    )
    estimated_output_tokens = max(1, len(contract.as_text()) // 4)
    return {
        "case": name,
        "requested_strategy": requested_strategy,
        "selected_strategy": result.route.selected_strategy,
        "final_strategy": result.final_strategy,
        "fallback_reason": result.fallback_reason,
        "latency_ms": latency_ms,
        "request_count": 1 if result.final_strategy == "one_shot" else chunk_count,
        "estimated_input_tokens": estimated_input_tokens,
        "estimated_output_tokens": estimated_output_tokens,
        "estimated_cost_proxy_tokens": estimated_input_tokens + estimated_output_tokens,
        "summary_valid": bool(contract.sections)
        and "Document processed in " not in contract.as_text(),
        "supervisor_passed": bool(validation.get("supervisor_passed")),
        "pdf_valid": pdf_bytes.startswith(b"%PDF-"),
        "section_count": len(contract.sections),
        "route_reason": result.route.reason,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark chunked vs one-shot-first summary strategies offline."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a tabular text report.",
    )
    args = parser.parse_args()

    sample = _load_sample_fixture()
    cases = [
        _benchmark_case(
            name="small_clean_chunked",
            payload=sample,
            requested_strategy="chunked",
            one_shot_token_threshold=120_000,
        ),
        _benchmark_case(
            name="small_clean_one_shot",
            payload=sample,
            requested_strategy="one_shot",
            one_shot_token_threshold=120_000,
        ),
        _benchmark_case(
            name="medium_clean_one_shot",
            payload=_build_medium_payload(),
            requested_strategy="one_shot",
            one_shot_token_threshold=120_000,
        ),
        _benchmark_case(
            name="large_noisy_auto",
            payload=_build_large_noisy_payload(),
            requested_strategy="auto",
            one_shot_token_threshold=120_000,
        ),
    ]

    if args.json:
        print(json.dumps(cases, indent=2))
        return

    headers = (
        "case",
        "requested_strategy",
        "final_strategy",
        "latency_ms",
        "request_count",
        "estimated_input_tokens",
        "estimated_output_tokens",
        "estimated_cost_proxy_tokens",
        "summary_valid",
        "pdf_valid",
        "route_reason",
    )
    print("\t".join(headers))
    for row in cases:
        print("\t".join(str(row[key]) for key in headers))


if __name__ == "__main__":
    main()
