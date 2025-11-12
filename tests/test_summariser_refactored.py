from __future__ import annotations

from dataclasses import dataclass
import json
import sys
from typing import Dict, Any
from types import SimpleNamespace

import pytest

from src.errors import SummarizationError
from src.services.summariser_refactored import (
    RefactoredSummariser,
    ChunkSummaryBackend,
    OpenAIResponsesBackend,
    HeuristicChunkBackend,
    _split_gcs_uri,
    _merge_dicts,
    _load_input_payload,
    _cli,
)


@dataclass
class StubBackend(ChunkSummaryBackend):
    responses: Dict[int, Dict[str, Any]]

    def summarise_chunk(
        self,
        *,
        chunk_text: str,
        chunk_index: int,
        total_chunks: int,
        estimated_tokens: int,
    ) -> Dict[str, Any]:
        payload = self.responses.get(chunk_index)
        if not payload:
            fallback_index = max(self.responses)
            payload = self.responses[fallback_index]
        # Include a snippet of the chunk to ensure backend sees the same data across invocations.
        result = dict(payload)
        result.setdefault(
            "overview", f"Chunk {chunk_index} covers {len(chunk_text.split())} words."
        )
        return result


def test_refactored_summary_structure_and_length() -> None:
    text = (
        "Patient Jane Doe visited the clinic complaining of persistent migraines. "
        "Neurological examination revealed no focal deficits. MRI performed in March 2024 was normal. "
        "Blood pressure measured at 138/88 mmHg. Provider Dr. Alicia Carter discussed lifestyle modifications. "
        "Sumatriptan 50 mg prescribed to be taken at onset of migraine. Follow-up arranged in 6 weeks. "
        "Patient also reports mild anxiety managed with cognitive behavioural therapy."
    ) * 5

    backend = StubBackend(
        responses={
            1: {
                "overview": "Follow-up neurology visit for chronic migraines without aura.",
                "key_points": [
                    "Patient reports increased migraine frequency impacting daily activities.",
                    "Normal neurological examination with stable vitals.",
                ],
                "clinical_details": [
                    "Blood pressure 138/88 mmHg; neurological exam non-focal.",
                    "MRI from March 2024 reviewed and remains normal.",
                ],
                "care_plan": [
                    "Continue sumatriptan 50 mg at migraine onset and track response.",
                    "Reinforce hydration, sleep hygiene, and headache diary usage.",
                ],
                "diagnoses": ["G43.709 Chronic migraine without aura"],
                "providers": ["Dr. Alicia Carter"],
                "medications": ["Sumatriptan 50 mg as needed"],
            },
            2: {
                "key_points": [
                    "Patient engaging in cognitive behavioural therapy for anxiety symptoms.",
                ],
                "clinical_details": [
                    "Patient denies visual changes, motor weakness, or speech disturbance.",
                    "Reports mild anxiety managed through behavioural interventions.",
                ],
                "care_plan": [
                    "Schedule follow-up neurology visit in 6 weeks to reassess frequency and treatment response.",
                ],
                "diagnoses": ["F41.9 Anxiety disorder, unspecified"],
                "providers": ["Clinic behavioural health team"],
                "medications": ["Cognitive behavioural therapy"],
            },
        }
    )

    summariser = RefactoredSummariser(
        backend=backend, target_chars=300, max_chars=420, overlap_chars=60
    )
    summary = summariser.summarise(text, doc_metadata={"facility": "MCC Neurology"})
    medical_summary = summary["Medical Summary"]

    assert "Intro Overview:" in medical_summary
    assert "Key Points:" in medical_summary
    assert "Detailed Findings:" in medical_summary
    assert "Care Plan & Follow-Up:" in medical_summary
    assert len(medical_summary) >= summariser.min_summary_chars

    assert isinstance(summary["intro_overview"], list)
    assert summary["intro_overview"]
    assert isinstance(summary["key_points"], list)
    assert summary["key_points"]
    assert isinstance(summary["detailed_findings"], list)
    assert summary["detailed_findings"]
    assert isinstance(summary["care_plan"], list)
    assert summary["care_plan"]

    diagnoses_lines = summary["_diagnoses_list"].splitlines()
    assert "G43.709 Chronic migraine without aura" in diagnoses_lines[0]
    assert summary["_providers_list"].strip().startswith("Dr. Alicia Carter")


def test_refactored_summary_requires_non_empty_text() -> None:
    backend = StubBackend(responses={1: {"overview": "Empty"}})
    summariser = RefactoredSummariser(backend=backend)
    with pytest.raises(SummarizationError):
        summariser.summarise("   ")


def test_compose_summary_pads_short_outputs() -> None:
    backend = StubBackend(
        responses={
            1: {
                "overview": "Brief visit for vaccination update.",
                "key_points": ["Tdap booster administered."],
                "clinical_details": ["No adverse reactions documented."],
                "care_plan": ["Monitor for injection site soreness."],
            }
        }
    )
    summariser = RefactoredSummariser(backend=backend, min_summary_chars=300)
    result = summariser.summarise("Tdap booster provided.")
    assert len(result["Medical Summary"]) >= 300


def test_cross_section_dedupe_removes_duplicate_lines() -> None:
    repeated_line = "Patient denies chest discomfort at rest."
    backend = StubBackend(
        responses={
            1: {
                "overview": repeated_line,
                "key_points": [
                    repeated_line,
                    "Follow-up with cardiology arranged.",
                ],
                "clinical_details": [
                    repeated_line,
                    "Imaging study demonstrates no acute findings.",
                ],
                "care_plan": ["Continue home blood pressure monitoring and medication log."],
            }
        }
    )
    summariser = RefactoredSummariser(
        backend=backend,
        min_summary_chars=150,
        target_chars=400,
        max_chars=800,
    )
    payload_text = ("Patient denies chest pain post procedure. " * 40).strip()
    summary = summariser.summarise(payload_text)

    assert repeated_line in summary["intro_overview"]
    assert repeated_line not in summary["key_points"]
    assert "Imaging study demonstrates no acute findings." in summary[
        "detailed_findings"
    ]
    assert repeated_line not in summary["detailed_findings"]


def test_intro_uses_facility_and_entity_lists_require_filtered_content() -> None:
    backend = StubBackend(
        responses={
            1: {
                "overview": "Document processed in 2 chunk(s).",
                "key_points": ["Patient discharged the same day in stable condition."],
                "clinical_details": ["Vitals remained stable throughout observation."],
                "care_plan": ["Routine follow-up in cardiology clinic within 2 weeks."],
            }
        }
    )
    summariser = RefactoredSummariser(
        backend=backend,
        min_summary_chars=150,
        target_chars=400,
        max_chars=800,
    )
    text = ("Same day surgery discharge summary. " * 40).strip()
    summary = summariser.summarise(
        text, doc_metadata={"facility": "Sunrise Medical Center"}
    )

    assert summary["intro_overview"] == ["Source: Sunrise Medical Center."]
    assert summary["_diagnoses_list"].strip() == ""
    assert summary["_providers_list"].strip() == ""
    assert summary["_medications_list"].strip() == ""
    assert "document processed" not in summary["Medical Summary"].lower()


def test_openai_backend_schema_mismatch_raises(monkeypatch):
    backend = OpenAIResponsesBackend(model="gpt-test", api_key="key")

    def _fake_create(**_: Any):
        return SimpleNamespace(
            output=[
                SimpleNamespace(
                    content=[
                        SimpleNamespace(
                            type="output_text",
                            text=json.dumps(
                                {
                                    "overview": "Example chunk without schema version",
                                    "key_points": [],
                                    "clinical_details": [],
                                    "care_plan": [],
                                    "diagnoses": [],
                                    "providers": [],
                                    "medications": [],
                                }
                            ),
                        )
                    ]
                )
            ]
        )

    class _FakeClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key
            self.responses = SimpleNamespace(create=_fake_create)

    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(OpenAI=lambda api_key: _FakeClient(api_key)),
    )

    with pytest.raises(SummarizationError):
        backend.summarise_chunk(
            chunk_text="data", chunk_index=1, total_chunks=1, estimated_tokens=500
        )


def test_heuristic_backend_includes_schema_version():
    backend = HeuristicChunkBackend()
    result = backend.summarise_chunk(
        chunk_text="Patient seen for hypertension follow-up. Blood pressure well controlled.",
        chunk_index=1,
        total_chunks=1,
        estimated_tokens=120,
    )
    assert result["schema_version"] == OpenAIResponsesBackend.CHUNK_SCHEMA_VERSION


def test_heuristic_backend_entity_extraction():
    backend = HeuristicChunkBackend()
    text = (
        "Dr. John Smith reviewed the patient's hypertension and diabetes treatment plan. "
        "Lisinopril 20 mg tablet prescribed daily with Hydrotherapy diet adjustments. "
        "Follow-up recommended in six weeks to monitor medication efficacy."
    )
    result = backend.summarise_chunk(
        chunk_text=text,
        chunk_index=1,
        total_chunks=1,
        estimated_tokens=200,
    )
    assert any("John Smith" in provider for provider in result["providers"])
    assert any("Lisinopril 20 mg" in med for med in result["medications"])
    assert result["care_plan"], "Care plan should be populated from follow-up sentence"


def test_split_gcs_uri_validation():
    bucket, blob = _split_gcs_uri("gs://bucket/path/file.json")
    assert bucket == "bucket"
    assert blob == "path/file.json"
    with pytest.raises(SummarizationError):
        _split_gcs_uri("http://invalid/path")
    with pytest.raises(SummarizationError):
        _split_gcs_uri("gs://bucket")


def test_merge_dicts_overrides_keys():
    base = {"a": 1, "b": 2}
    patch = {"b": 3, "c": 4}
    merged = _merge_dicts(base, patch)
    assert merged == {"a": 1, "b": 3, "c": 4}
    assert base["b"] == 2  # original not mutated


def test_load_input_payload_variants(tmp_path):
    data = {
        "text": "Example text body",
        "pages": [{"text": "Example page 1"}],
        "metadata": {"source": "unit-test"},
    }
    payload_path = tmp_path / "input.json"
    payload_path.write_text(json.dumps(data), encoding="utf-8")
    text, metadata, pages = _load_input_payload(payload_path)
    assert text.startswith("Example")
    assert metadata["source"] == "unit-test"
    assert pages and pages[0]["text"] == "Example page 1"

    payload_path.write_text(
        json.dumps({"pages": [{"text": "Only pages"}]}), encoding="utf-8"
    )
    rewritten_text, _, rewritten_pages = _load_input_payload(payload_path)
    assert rewritten_text.strip() == "Only pages"
    assert rewritten_pages and rewritten_pages[0]["text"] == "Only pages"

    payload_path.write_text(json.dumps({"invalid": "structure"}), encoding="utf-8")
    with pytest.raises(SummarizationError):
        _load_input_payload(payload_path)


def test_load_input_payload_gcs_blob(monkeypatch):
    payload = {
        "text": "Remote text body",
        "pages": [{"text": "Remote text body"}],
        "metadata": {"source": "gcs"},
    }
    blob_bytes = json.dumps(payload).encode("utf-8")

    class _StubBlob:
        def __init__(self, name: str, data: bytes | None) -> None:
            self.name = name
            self._data = data

        def download_as_bytes(self) -> bytes:
            if self._data is None:
                raise FileNotFoundError("missing blob")
            return self._data

    class _StubBucket:
        def __init__(self, blobs: Dict[str, bytes | None]) -> None:
            self._blobs = blobs

        def blob(self, name: str) -> _StubBlob:
            return _StubBlob(name, self._blobs.get(name))

    class _StubClient:
        def __init__(self, blobs: Dict[str, bytes | None]) -> None:
            self._blobs = blobs

        def bucket(self, _: str) -> _StubBucket:
            return _StubBucket(self._blobs)

        def list_blobs(
            self, *_args, **_kwargs
        ):  # pragma: no cover - not used in this scenario
            return []

    monkeypatch.setattr(
        "google.cloud.storage.Client",
        lambda: _StubClient({"ocr/job/aggregate.json": blob_bytes}),
    )

    text, metadata, pages = _load_input_payload("gs://bucket/ocr/job/aggregate.json")
    assert text == "Remote text body"
    assert metadata["source"] == "gcs"
    assert pages and pages[0]["text"] == "Remote text body"


def test_load_input_payload_gcs_prefix_fallback(monkeypatch):
    shard_payload = {
        "document": {
            "pages": [{"text": "Shard text"}],
            "metadata": {"shard": "0"},
        }
    }
    shard_bytes = json.dumps(shard_payload).encode("utf-8")

    class _StubBlob:
        def __init__(self, name: str, data: bytes | None) -> None:
            self.name = name
            self._data = data

        def download_as_bytes(self) -> bytes:
            if self._data is None:
                raise FileNotFoundError("missing blob")
            return self._data

    class _StubBucket:
        def __init__(self, blobs: Dict[str, bytes | None]) -> None:
            self._blobs = blobs

        def blob(self, name: str) -> _StubBlob:
            return _StubBlob(name, self._blobs.get(name))

    class _StubClient:
        def __init__(self, blobs: Dict[str, bytes | None]) -> None:
            self._blobs = blobs

        def bucket(self, _: str) -> _StubBucket:
            return _StubBucket(self._blobs)

        def list_blobs(self, _bucket: str, prefix: str):
            return [
                _StubBlob(name, data)
                for name, data in self._blobs.items()
                if name.startswith(prefix)
            ]

    blobs = {
        "ocr/job/aggregate.json": None,
        "ocr/job/0/shard.json": shard_bytes,
    }
    monkeypatch.setattr("google.cloud.storage.Client", lambda: _StubClient(blobs))

    text, metadata, pages = _load_input_payload("gs://bucket/ocr/job/aggregate.json")
    assert "Shard text" in text
    assert pages and pages[0]["text"] == "Shard text"
    assert metadata.get("shard") == "0"


def test_openai_backend_falls_back_to_heuristic(monkeypatch):
    backend = OpenAIResponsesBackend(model="gpt-test", api_key="test-key")

    class _StubClient:
        class responses:
            @staticmethod
            def create(*_args, **_kwargs):
                raise TypeError("unexpected keyword argument 'response_format'")

    monkeypatch.setitem(
        sys.modules, "openai", SimpleNamespace(OpenAI=lambda api_key: _StubClient())
    )

    result = backend.summarise_chunk(
        chunk_text="Patient reviewed for hypertension follow-up. Blood pressure improved with medication.",
        chunk_index=1,
        total_chunks=1,
        estimated_tokens=120,
    )
    assert result["schema_version"] == OpenAIResponsesBackend.CHUNK_SCHEMA_VERSION
    assert "hypertension" in " ".join(result["diagnoses"]).lower()


def test_cli_fallback_to_heuristic_backend(monkeypatch, tmp_path):
    input_path = tmp_path / "payload.json"
    input_payload = {
        "text": "Patient seen for follow-up. Blood pressure improving with medication adherence.",
        "metadata": {"job_id": "cli-test"},
    }
    input_path.write_text(json.dumps(input_payload), encoding="utf-8")
    output_path = tmp_path / "summary.json"

    original_summarise = RefactoredSummariser.summarise

    def _fake_summarise(self, text: str, *, doc_metadata: Dict[str, Any] | None = None):
        if isinstance(self.backend, OpenAIResponsesBackend):
            raise SummarizationError("upstream failure")
        return original_summarise(self, text, doc_metadata=doc_metadata)

    monkeypatch.setattr(
        "src.services.summariser_refactored.RefactoredSummariser.summarise",
        _fake_summarise,
    )
    monkeypatch.setattr(
        "src.services.summariser_refactored.CommonSenseSupervisor.validate",
        lambda self, **_: {"supervisor_passed": True},
    )

    _cli(
        [
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--api-key",
            "dummy-key",
        ]
    )

    summary_output = json.loads(output_path.read_text(encoding="utf-8"))
    assert summary_output["Medical Summary"]
    assert "blood pressure" in summary_output["Medical Summary"].lower()
