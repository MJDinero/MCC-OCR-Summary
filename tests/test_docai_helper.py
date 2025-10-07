import pytest
from types import SimpleNamespace
from google.api_core import exceptions as gexc

from src.services.docai_helper import OCRService
from src.errors import OCRServiceError, ValidationError
from src.config import AppConfig

VALID_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


class DummyClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def process_document(self, request):  # noqa: D401
        self.calls += 1
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(document=result)


def make_cfg():
    return AppConfig(project_id="proj", DOC_AI_LOCATION="us", DOC_AI_OCR_PROCESSOR_ID="pid")


def test_success_first_try():
    client = DummyClient([{"text": "Hello", "pages": [{"text": "Hello"}]}])
    svc = OCRService("pid", config=make_cfg(), client_factory=lambda _ep: client)
    out = svc.process(VALID_PDF)
    assert out["text"] == "Hello"
    assert client.calls == 1


def test_retry_then_success():
    client = DummyClient([
        gexc.ServiceUnavailable("unavail"),
        {"text": "World", "pages": [{"text": "World"}]},
    ])
    svc = OCRService("pid", config=make_cfg(), client_factory=lambda _ep: client)
    out = svc.process(VALID_PDF)
    assert out["text"] == "World"
    assert client.calls == 2


def test_permanent_failure():
    client = DummyClient([gexc.InvalidArgument("bad")])
    svc = OCRService("pid", config=make_cfg(), client_factory=lambda _ep: client)
    with pytest.raises(OCRServiceError):
        svc.process(VALID_PDF)
    assert client.calls == 1


def test_validation_error_propagates():
    client = DummyClient([])
    svc = OCRService("pid", config=make_cfg(), client_factory=lambda _ep: client)
    with pytest.raises(ValidationError):
        svc.process(b"not a pdf")
