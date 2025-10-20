from src.services.summariser import StructuredSummariser
from src.services.supervisor import CommonSenseSupervisor
from src.utils.summary_thresholds import compute_summary_min_chars


class DummyBackend:
    def __init__(self):
        # Return minimal structured fields with synthetic variation
        self.calls = 0
    def summarise(self, text: str):  # returns per-chunk structured JSON
        self.calls += 1
        return {
            'provider_seen': f'Dr Smith chunk {self.calls}',
            'reason_for_visit': 'Follow up',
            'clinical_findings': f'Findings {self.calls}',
            'treatment_plan': 'Plan stable',
            'diagnoses': ['D1', 'D2'],
            'providers': ['Dr Smith'],
            'medications': ['MedA']
        }


def test_large_input_multichunk_merge():
    backend = DummyBackend()
    s = StructuredSummariser(backend, chunk_target_chars=100, chunk_hard_max=120, multi_chunk_threshold=120)
    big_text = ("word " * 800)  # large enough to force many chunks at 100 char target
    out = s.summarise(big_text)
    assert out['Medical Summary']
    # Side channel lists preserved
    assert '_diagnoses_list' in out and 'D1' in out['_diagnoses_list']
    # At least 2 chunk calls
    assert backend.calls >= 2


def test_short_documents_pass_with_dynamic_floor(monkeypatch):
    monkeypatch.delenv("MIN_SUMMARY_CHARS", raising=False)
    monkeypatch.delenv("MIN_SUMMARY_DYNAMIC_RATIO", raising=False)

    ocr_text = "a" * 200  # yields dynamic threshold of max(120, int(0.35 * 200)) == 120
    required = compute_summary_min_chars(len(ocr_text))
    assert required == 120

    supervisor = CommonSenseSupervisor(simple=True)
    summary_payload = {"Medical Summary": "b" * required}
    result = supervisor.validate(
        ocr_text=ocr_text,
        summary=summary_payload,
        doc_stats={"pages": 1, "text_length": len(ocr_text), "file_size_mb": 0.1},
    )
    assert result["checks"]["length_ok"] is True
    assert result["supervisor_passed"] is True
