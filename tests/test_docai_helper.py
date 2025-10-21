import logging
from types import SimpleNamespace

import pytest
from google.api_core import exceptions as gexc

from src.services.docai_helper import OCRService, run_splitter, run_batch_ocr
from src.errors import OCRServiceError, ValidationError
from src.config import AppConfig, get_config
from src.services.pipeline import InMemoryStateStore, PipelineJobCreate, PipelineStatus

# pylint: disable=unused-argument,protected-access

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


def test_docai_logs_success(caplog):
    client = DummyClient([{"text": "Hello", "pages": [{"text": "Hello"}]}])
    svc = OCRService("pid", config=make_cfg(), client_factory=lambda _ep: client)
    caplog.set_level(logging.INFO, logger="ocr_service")
    result = svc.process(VALID_PDF, trace_id="trace-xyz")
    assert result["text"] == "Hello"
    events = {record.event for record in caplog.records if getattr(record, 'event', None)}
    assert {"docai_call_start", "docai_call_success"}.issubset(events)


def test_run_splitter_updates_state_and_manifest(monkeypatch):
    monkeypatch.setenv("PROJECT_ID", "proj")
    monkeypatch.setenv("REGION", "us")
    monkeypatch.setenv("CMEK_KEY_NAME", "projects/demo/locations/us-central1/keyRings/test/cryptoKeys/test-key")
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
                    "gcs_output_config": {"gcs_uri": "gs://intake-bucket/split/job123/"},
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
    assert request["document_output_config"]["gcs_output_config"]["kms_key_name"].endswith("test-key")
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
            shard_uri = request["input_documents"]["gcs_documents"]["documents"][0]["gcs_uri"]
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
