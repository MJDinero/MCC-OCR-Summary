from src.services.summariser import Summariser


class ConstantChunkBackend:
    def summarise(self, _text: str):
        # Simulate per-chunk structured JSON output
        return {
            "provider_seen": "Dr John Smith",
            "reason_for_visit": "Routine annual examination",
            "clinical_findings": "Blood pressure mildly elevated",
            "treatment_plan": "Initiate lifestyle modifications and monitor",
            "diagnoses": [
                "I10 Hypertension",
                "I10 Hypertension",
            ],  # intentional duplicate
            "providers": ["Dr John Smith", "Dr John Smith"],  # duplicate
            "medications": "Lisinopril, Lisinopril",  # duplicate in comma string form
        }


def test_medical_summary_multi_chunk_merge():
    # Long text to force multiple chunks (~>3000 chars)
    long_text = ("word " * 1600).strip()
    s = Summariser(ConstantChunkBackend())
    result = s.summarise(long_text)
    medical = result["Medical Summary"]
    # Section headers present
    for header in [
        "Provider Seen:",
        "Reason for Visit:",
        "Clinical Findings:",
        "Treatment / Follow-Up Plan:",
        "Diagnoses:",
        "Providers:",
        "Medications / Prescriptions:",
    ]:
        assert header in medical
    # Duplicates deduplicated
    assert medical.count("I10 Hypertension") == 1
    assert medical.count("Dr John Smith") == 2  # once in narrative + once in list
    # Ensure list dash formatting
    assert "- I10 Hypertension" in medical
    assert "- Lisinopril" in medical
