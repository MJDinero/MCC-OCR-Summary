from src.utils.redact import REDACTION_TOKEN, redact_mapping, redact_text


def test_redact_text_patterns():
    text = "Call me at 555-123-4567 or email test@example.com with SSN 123-45-6789."
    result = redact_text(text)
    assert REDACTION_TOKEN in result
    assert "123-45-6789" not in result


def test_redact_mapping_nested():
    payload = {"patient": {"phone": "555 123 4567", "notes": ["email: user@foo.com"]}}
    redacted = redact_mapping(payload)
    assert redacted["patient"]["phone"] == REDACTION_TOKEN
    assert redacted["patient"]["notes"][0] == f"email: {REDACTION_TOKEN}"
