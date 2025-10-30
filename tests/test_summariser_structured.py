from src.services.summariser import Summariser


class DummyBackend:
    def summarise(self, text: str):
        # Returns snake_case keys per new schema; Summariser maps to display headings
        return {
            "patient_info": "P",
            "medical_summary": "M",
            "billing_highlights": "B",
            "legal_notes": "L",
        }


def test_summariser_pass_through():
    s = Summariser(DummyBackend())
    out = s.summarise("some text")
    assert out["Medical Summary"] == "M"
