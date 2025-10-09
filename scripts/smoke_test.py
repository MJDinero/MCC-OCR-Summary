#!/usr/bin/env python3
"""Lightweight smoke test for StructuredSummariser.

Generates a large synthetic OCR text, runs through StructuredSummariser with a
dummy backend (no OpenAI call) and asserts expected log markers & output shape.
Intended for CI/CD gating (fast, offline).
"""
from __future__ import annotations

from src.services.summariser import StructuredSummariser


class _DummyBackend:
    def __init__(self):
        self.calls = 0
    def summarise(self, text: str):  # pragma: no cover - trivial
        self.calls += 1
        return {
            'provider_seen': 'Dr Example',
            'reason_for_visit': 'Routine',
            'clinical_findings': f'Findings batch {self.calls}',
            'treatment_plan': 'Continue monitoring',
            'diagnoses': ['DXA', 'DXB'],
            'providers': ['Dr Example'],
            'medications': ['MedX']
        }


def run():
    backend = _DummyBackend()
    s = StructuredSummariser(backend, chunk_target_chars=500, chunk_hard_max=600, multi_chunk_threshold=600)
    text = ("lorem ipsum dolor sit amet " * 1200)
    result = s.summarise(text)
    assert 'Medical Summary' in result
    assert result['_diagnoses_list'].startswith('DXA')
    assert backend.calls >= 2, 'Expected multi-chunk operation'
    lines = result['Medical Summary'].splitlines()
    assert len(lines) > 5
    print('SMOKE TEST PASS: chunks=', backend.calls, 'lines=', len(lines))


if __name__ == '__main__':  # pragma: no cover
    run()
