from src.services.summariser import Summariser


class NewSchemaBackend:
    def summarise(self, _text: str):
        # Simulate backend returning new per-chunk schema with partial data
        return {
            "provider_seen": "Dr Alice",
            "reason_for_visit": "Follow-up hypertension",
            "clinical_findings": "BP 140/90",
            "treatment_plan": "Increase exercise",
            "diagnoses": ["I10 Hypertension"],
            "providers": ["Dr Alice"],
            "medications": ["Lisinopril"],
        }


def test_new_schema_single_chunk_mapping():
    s = Summariser(NewSchemaBackend())
    result = s.summarise("short text")
    assert "Medical Summary" in result
    ms = result["Medical Summary"]
    assert "Provider Seen:" in ms
    assert "- I10 Hypertension" in ms
    assert "- Lisinopril" in ms
