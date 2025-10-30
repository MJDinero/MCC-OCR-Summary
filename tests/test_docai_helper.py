import pytest
from types import SimpleNamespace
from google.api_core import exceptions as gexc

from src.services.docai_helper import (
    OCRService,
    run_splitter,
    run_batch_ocr,
    clean_ocr_output,
)
from src.errors import OCRServiceError, ValidationError
from src.config import AppConfig, get_config
from src.services.pipeline import InMemoryStateStore, PipelineJobCreate, PipelineStatus

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
    return AppConfig(
        project_id="proj", DOC_AI_LOCATION="us", DOC_AI_OCR_PROCESSOR_ID="pid"
    )


def test_clean_ocr_output_strips_transport_headers():
    raw = (
        "To: +12145551234\n"
        "Fax: 214-555-9999\n"
        "Page: 2 of 5\n"
        "Clinical summary states patient followed up with provider.\n"
        "From: Records Desk\n"
        "Affidavit of Custodian of Records\n"
        "State of Texas, County of Dallas\n"
        "Sworn statement and true and correct copy attached hereto.\n"
        "Invoice 12345 Total Charges\n"
        "Commission expires March 2027\n"
        "Regular course of business attestment by custodian\n"
        "Payer: Blue Cross PPO\n"
        "Health Plan ID: AB-12345\n"
        "Follow the instructions from your healthcare provider and call 911 if symptoms worsen.\n"
        "Signs of infection such as fever or redness should prompt immediate medical attention.\n"
        "Follow-up scheduled next month."
    )
    cleaned = clean_ocr_output(raw)
    assert "Clinical summary states patient followed up with provider." in cleaned
    assert "Follow-up scheduled next month." in cleaned
    assert "To:" not in cleaned
    assert "Fax" not in cleaned
    assert "Page" not in cleaned
    assert "Affidavit" not in cleaned
    assert "County of Dallas" not in cleaned
    assert "Invoice" not in cleaned
    assert "true and correct copy" not in cleaned.lower()
    assert "Commission expires" not in cleaned
    assert "regular course of business" not in cleaned.lower()
    assert "Payer:" not in cleaned
    assert "Health Plan ID" not in cleaned
    assert "Follow the instructions" not in cleaned
    assert "Signs of infection" not in cleaned


def test_success_first_try():
    client = DummyClient([{"text": "Hello", "pages": [{"text": "Hello"}]}])
    svc = OCRService("pid", config=make_cfg(), client_factory=lambda _ep: client)
    out = svc.process(VALID_PDF)
    assert out["text"] == "Hello"
    assert client.calls == 1


def test_retry_then_success():
    client = DummyClient(
        [
            gexc.ServiceUnavailable("unavail"),
            {"text": "World", "pages": [{"text": "World"}]},
        ]
    )
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


def test_run_splitter_updates_state_and_manifest(monkeypatch):
    monkeypatch.setenv("PROJECT_ID", "proj")
    monkeypatch.setenv("REGION", "us")
    monkeypatch.setenv(
        "CMEK_KEY_NAME",
        "projects/demo/locations/us-central1/keyRings/test/cryptoKeys/test-key",
    )
    get_config.cache_clear()
    store = InMemoryStateStore()
    job = store.create_job(
        PipelineJobCreate(
            bucket="intake-bucket",
            object_name="source.pdf",
            generation="1",
        )
    )

    uploads = []

    def _fake_upload(uri, payload, if_generation_match=None):
        uploads.append((uri, payload, if_generation_match))
        return 123

    monkeypatch.setattr("src.services.docai_helper._gcs_upload_json", _fake_upload)

    class _StubOperation:
        def __init__(self):
            self.name = "operations/split-1"

        def result(self, timeout=None):
            return {
                "document_output_config": {
                    "gcs_output_config": {
                        "gcs_uri": "gs://intake-bucket/split/job123/"
                    },
                },
                "shards": [
                    {"gcs_uri": "gs://intake-bucket/split/job123/0000.pdf"},
                    {"gcs_uri": "gs://intake-bucket/split/job123/0001.pdf"},
                ],
            }

    class _StubClient:
        def __init__(self):
            self.requests = []

        def batch_process_documents(self, request):
            self.requests.append(request)
            return _StubOperation()

    client = _StubClient()
    result = run_splitter(
        "gs://intake-bucket/source.pdf",
        processor_id="split-proc",
        project_id="proj",
        location="us",
        output_bucket="intake-bucket",
        output_prefix="split/job123/",
        manifest_name="manifest.json",
        job_id=job.job_id,
        trace_id="trace-abc",
        state_store=store,
        client=client,
        sleep_fn=lambda _: None,
    )

    assert client.requests, "Splitter should issue Document AI request"
    request = client.requests[0]
    assert request["document_output_config"]["gcs_output_config"][
        "kms_key_name"
    ].endswith("test-key")
    assert request["encryption_spec"]["kms_key_name"].endswith("test-key")
    assert result["manifest_uri"].endswith("manifest.json")
    assert len(result["shards"]) == 2
    assert uploads and uploads[0][0].endswith("manifest.json")

    updated_job = store.get_job(job.job_id)
    assert updated_job.status is PipelineStatus.SPLIT_DONE
    assert "split_shards" in updated_job.metadata
    assert updated_job.metadata["split_shards"][0].endswith("0000.pdf")
    get_config.cache_clear()


def test_run_batch_ocr_fanout_updates_metadata(monkeypatch):
    store = InMemoryStateStore()
    job = store.create_job(
        PipelineJobCreate(
            bucket="intake-bucket",
            object_name="source.pdf",
            generation="2",
        )
    )

    class _StubOCROperation:
        def __init__(self, output_uri: str, index: int):
            self._output_uri = output_uri
            self.name = f"operations/ocr-{index}"

        def result(self, timeout=None):
            return {
                "document_output_config": {
                    "gcs_output_config": {"gcs_uri": self._output_uri},
                }
            }

    class _StubOCRClient:
        def __init__(self):
            self.requests = []

        def batch_process_documents(self, request):
            shard_uri = request["input_documents"]["gcs_documents"]["documents"][0][
                "gcs_uri"
            ]
            dest_uri = request["document_output_config"]["gcs_output_config"]["gcs_uri"]
            op = _StubOCROperation(dest_uri, len(self.requests))
            self.requests.append((shard_uri, dest_uri))
            return op

    shards = [
        "gs://intake-bucket/split/job123/0000.pdf",
        "gs://intake-bucket/split/job123/0001.pdf",
    ]

    client = _StubOCRClient()
    result = run_batch_ocr(
        shards,
        processor_id="ocr-proc",
        project_id="proj",
        location="us",
        output_bucket="output-bucket",
        output_prefix="ocr/job123/",
        job_id=job.job_id,
        trace_id="trace-xyz",
        state_store=store,
        client=client,
        max_concurrency=2,
        sleep_fn=lambda _: None,
    )

    assert len(client.requests) == len(shards)
    assert len(result["outputs"]) == len(shards)
    output_uris = {entry["ocr_output_uri"] for entry in result["outputs"]}
    assert output_uris == {
        "gs://output-bucket/ocr/job123/0000/",
        "gs://output-bucket/ocr/job123/0001/",
    }

    updated_job = store.get_job(job.job_id)
    assert updated_job.status is PipelineStatus.OCR_DONE
    metadata_outputs = updated_job.metadata.get("ocr_outputs")
    assert metadata_outputs and len(metadata_outputs) == len(shards)


def test_run_batch_ocr_propagates_poll_errors(monkeypatch):
    store = InMemoryStateStore()
    job = store.create_job(
        PipelineJobCreate(
            bucket="intake-bucket",
            object_name="source.pdf",
            generation="3",
        )
    )

    class _StubFailureClient:
        def __init__(self):
            self.requests = 0

        def batch_process_documents(self, request):
            self.requests += 1
            return object()

    def _failing_poll(operation, *, stage, job_id, trace_id, sleep_fn):
        raise RuntimeError("ocr shard failed")

    monkeypatch.setattr("src.services.docai_helper._poll_operation", _failing_poll)
    published: list[dict] = []
    monkeypatch.setattr(
        "src.services.docai_helper.publish_pipeline_failure",
        lambda **kwargs: published.append(kwargs),
    )

    with pytest.raises(RuntimeError):
        run_batch_ocr(
            ["gs://intake-bucket/split/job123/0000.pdf"],
            processor_id="ocr-proc",
            project_id="proj",
            location="us",
            output_bucket="output-bucket",
            output_prefix="ocr/job123/",
            job_id=job.job_id,
            trace_id="trace-err",
            state_store=store,
            client=_StubFailureClient(),
            max_concurrency=1,
            sleep_fn=lambda _: None,
        )

    failed_job = store.get_job(job.job_id)
    assert failed_job.status is PipelineStatus.FAILED
    assert failed_job.last_error["stage"] == "ocr"
    assert published and published[0]["stage"] == "DOC_AI_OCR"
    assert published[0]["job_id"] == job.job_id
