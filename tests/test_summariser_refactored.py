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
        default_reason = f"Chunk {chunk_index} covers {len(chunk_text.split())} words."
        if "reason_for_visit" not in result:
            result["reason_for_visit"] = default_reason
        return result


class ProviderEmptyBackend(ChunkSummaryBackend):
    def summarise_chunk(
        self,
        *,
        chunk_text: str,
        chunk_index: int,
        total_chunks: int,
        estimated_tokens: int,
    ) -> Dict[str, Any]:
        return {
            "provider_seen": [],
            "reason_for_visit": ["Routine evaluation for chronic pain management."],
            "clinical_findings": ["Pain score improved from prior visit."],
            "treatment_plan": ["Continue home exercise plan and monitor symptoms."],
            "diagnoses": ["Chronic pain syndrome"],
            "healthcare_providers": [],
            "medications": ["Gabapentin 300 mg nightly"],
        }


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
                "provider_seen": ["Dr. Alicia Carter, Neurology"],
                "overview": "Follow-up neurology visit for chronic migraines without aura.",
                "reason_for_visit": [
                    "Patient reports increased migraine frequency impacting daily activities.",
                    "Normal neurological examination with stable vitals.",
                ],
                "clinical_findings": [
                    "Blood pressure 138/88 mmHg; neurological exam non-focal.",
                    "MRI from March 2024 reviewed and remains normal.",
                ],
                "treatment_plan": [
                    "Continue sumatriptan 50 mg at migraine onset and track response.",
                    "Reinforce hydration, sleep hygiene, and headache diary usage.",
                ],
                "diagnoses": ["G43.709 Chronic migraine without aura"],
                "healthcare_providers": ["Dr. Alicia Carter"],
                "medications": ["Sumatriptan 50 mg as needed"],
            },
            2: {
                "reason_for_visit": [
                    "Patient engaging in cognitive behavioural therapy for anxiety symptoms.",
                ],
                "clinical_findings": [
                    "Patient denies visual changes, motor weakness, or speech disturbance.",
                    "Reports mild anxiety managed through behavioural interventions.",
                ],
                "treatment_plan": [
                    "Schedule follow-up neurology visit in 6 weeks to reassess frequency and treatment response.",
                ],
                "diagnoses": ["F41.9 Anxiety disorder, unspecified"],
                "healthcare_providers": ["Clinic behavioural health team"],
                "medications": ["Cognitive behavioural therapy"],
            },
        }
    )

    summariser = RefactoredSummariser(
        backend=backend, target_chars=300, max_chars=420, overlap_chars=60
    )
    summary = summariser.summarise(text, doc_metadata={"facility": "MCC Neurology"})
    medical_summary = summary["Medical Summary"]

    assert "Provider Seen:" in medical_summary
    assert "Reason for Visit:" in medical_summary
    assert "Clinical Findings:" in medical_summary
    assert "Treatment / Follow-up Plan:" in medical_summary
    assert len(medical_summary) >= summariser.min_summary_chars

    assert isinstance(summary["provider_seen"], list)
    assert summary["provider_seen"]
    assert isinstance(summary["reason_for_visit"], list)
    assert summary["reason_for_visit"]
    assert isinstance(summary["clinical_findings"], list)
    assert summary["clinical_findings"]
    assert isinstance(summary["treatment_plan"], list)
    assert summary["treatment_plan"]

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
                "reason_for_visit": ["Tdap booster administered."],
                "clinical_findings": ["No adverse reactions documented."],
                "treatment_plan": ["Monitor for injection site soreness."],
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
                "reason_for_visit": [
                    repeated_line,
                    "Follow-up with cardiology arranged.",
                ],
                "clinical_findings": [
                    repeated_line,
                    "Imaging study demonstrates no acute findings.",
                ],
                "treatment_plan": [
                    "Continue home blood pressure monitoring and medication log."
                ],
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

    assert repeated_line not in summary["provider_seen"]
    assert repeated_line in summary["reason_for_visit"]
    assert (
        "Imaging study demonstrates no acute findings." in summary["clinical_findings"]
    )
    assert repeated_line not in summary["clinical_findings"]


def test_provider_seen_includes_facility_and_entity_lists_require_filtered_content() -> None:
    backend = StubBackend(
        responses={
            1: {
                "overview": "Document processed in 2 chunk(s).",
                "reason_for_visit": ["Patient discharged the same day in stable condition."],
                "clinical_findings": ["Vitals remained stable throughout observation."],
                "treatment_plan": ["Routine follow-up in cardiology clinic within 2 weeks."],
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

    assert summary["provider_seen"] == ["Facility: Sunrise Medical Center."]
    assert summary["_diagnoses_list"].strip() == ""
    assert summary["_providers_list"].strip() == ""
    assert summary["_medications_list"].strip() == ""
    assert "document processed" not in summary["Medical Summary"].lower()


def test_chunk_metadata_removed_from_provider_section() -> None:
    backend = StubBackend(
        responses={
            1: {
                "overview": "Document processed in 2 chunk(s). Follow-up visit today.",
                "reason_for_visit": ["Patient discharged same day."],
                "clinical_findings": ["Vitals stable throughout observation."],
                "treatment_plan": ["Routine follow-up in cardiology clinic within 2 weeks."],
            }
        }
    )
    summariser = RefactoredSummariser(
        backend=backend,
        min_summary_chars=150,
        target_chars=400,
        max_chars=800,
    )
    text = ("Same day surgery discharge summary. " * 30).strip()
    result = summariser.summarise(text)

    intro_lines = result["provider_seen"]
    assert all("document processed" not in line.lower() for line in intro_lines)
    assert "document processed" not in result["Medical Summary"].lower()


def test_entity_lists_exclude_admin_entries() -> None:
    backend = StubBackend(
        responses={
            1: {
                "overview": "Clinic visit for diabetes follow-up.",
                "diagnoses": [
                    "Consent on file",
                    "E11.9 Type 2 diabetes mellitus without complications",
                ],
                "healthcare_providers": [
                    "Clinic billing department",
                    "Dr. Priya Verma, Endocrinology",
                ],
                "medications": [
                    "Medication policy reviewed",
                    "Metformin 500 mg twice daily",
                ],
            }
        }
    )
    summariser = RefactoredSummariser(backend=backend, min_summary_chars=150)
    text = ("Diabetes visit summary. " * 30).strip()
    result = summariser.summarise(text)

    assert result["_diagnoses_list"].splitlines() == [
        "E11.9 Type 2 diabetes mellitus without complications"
    ]
    assert result["_providers_list"].splitlines() == ["Dr. Priya Verma, Endocrinology"]
    assert result["_medications_list"].splitlines() == ["Metformin 500 mg twice daily"]


def test_clean_merge_fragment_applies_to_all_sections() -> None:
    backend = StubBackend(
        responses={
            1: {
                "overview": "Document processed in 8 chunk(s). Admission addendum.",
                "reason_for_visit": [
                    "Structured Indices follow.",
                    "Patient evaluated by Dr. Rivera.",
                ],
                "clinical_findings": [
                    "Summary Notes: call 911 for emergencies.",
                    "Blood pressure 130/80 mmHg; neuro exam stable.",
                ],
                "treatment_plan": [
                    "Document processed in 8 chunk(s).",
                    "Follow-up with cardiology clinic in 4 weeks.",
                ],
            }
        }
    )
    summariser = RefactoredSummariser(backend=backend, min_summary_chars=150)
    payload_text = ("Extended narrative with vitals and impressions. " * 40).strip()
    summary = summariser.summarise(payload_text)

    def _section_contains_noise(lines: list[str]) -> bool:
        full = " ".join(lines).lower()
        return any(
            token in full
            for token in (
                "document processed in",
                "structured indices",
                "summary notes",
                "call 911",
            )
        )

    assert not _section_contains_noise(summary.get("provider_seen", []))
    assert summary["reason_for_visit"]
    assert not _section_contains_noise(summary["reason_for_visit"])
    assert summary["clinical_findings"]
    assert not _section_contains_noise(summary["clinical_findings"])
    assert summary["treatment_plan"]
    assert not _section_contains_noise(summary["treatment_plan"])
    assert "document processed in" not in summary["Medical Summary"].lower()


def test_intake_and_consent_language_removed_from_sections() -> None:
    backend = StubBackend(
        responses={
            1: {
                "provider_seen": [
                    "Intake form completed for medical record.",
                    "Dr. Priya Verma, Endocrinology",
                ],
                "reason_for_visit": [
                    "Consent for evaluation was signed.",
                    "Follow-up for chronic lumbar pain with radiculopathy.",
                ],
                "clinical_findings": [
                    "Intake questionnaire notes paperwork was reviewed.",
                    "MRI reviewed; no acute compression, paraspinal tenderness persists.",
                ],
                "treatment_plan": [
                    "Consent acknowledged for injection procedure.",
                    "Continue gabapentin and schedule PT in 2 weeks.",
                ],
                "diagnoses": [
                    "Consent on file for care plan.",
                    "M54.50 Low back pain, unspecified",
                ],
                "healthcare_providers": [
                    "Intake paperwork signed by clinic coordinator.",
                    "Dr. Priya Verma, Endocrinology",
                ],
                "medications": [
                    "Consent medication plan reviewed.",
                    "Gabapentin 100 mg nightly",
                ],
            }
        }
    )
    summariser = RefactoredSummariser(backend=backend, min_summary_chars=180)
    text = ("Extended lumbar evaluation with imaging updates. " * 40).strip()
    summary = summariser.summarise(text)

    for section_key in (
        "provider_seen",
        "reason_for_visit",
        "clinical_findings",
        "treatment_plan",
    ):
        joined = " ".join(summary.get(section_key, []))
        assert "consent" not in joined.lower()
        assert "intake" not in joined.lower()

    assert summary["_diagnoses_list"].splitlines() == ["M54.50 Low back pain, unspecified"]
    assert summary["_providers_list"].splitlines() == ["Dr. Priya Verma, Endocrinology"]
    assert summary["_medications_list"].splitlines() == ["Gabapentin 100 mg nightly"]
    assert "consent" not in summary["Medical Summary"].lower()

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
                                    "reason_for_visit": [],
                                    "clinical_findings": [],
                                    "treatment_plan": [],
                                    "diagnoses": [],
                                    "healthcare_providers": [],
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
    assert any("John Smith" in provider for provider in result["healthcare_providers"])
    assert any("Lisinopril 20 mg" in med for med in result["medications"])
    assert result["treatment_plan"], "Care plan should be populated from follow-up sentence"


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


def test_chunk_properties_enforce_bounds():
    backend = StubBackend(responses={1: {"overview": "Entry"}})
    summariser = RefactoredSummariser(backend=backend)
    summariser.chunk_target_chars = 256
    assert summariser.target_chars == 512
    summariser.chunk_hard_max = 512
    assert summariser.max_chars >= summariser.target_chars + 64


def test_merge_payload_normalises_values():
    aggregated = {
        "provider_seen": [],
        "reason_for_visit": [],
        "clinical_findings": [],
        "treatment_plan": [],
        "diagnoses": [],
        "healthcare_providers": [],
        "medications": [],
    }
    payload = {
        "provider_seen": "Dr. Gomez\nStructured Indices follow",
        "key_points": ("Follow-up requested",),
        "clinical_findings": {"vitals": "BP 120/80 noted"},
        "care_plan": ["Return to clinic in 6 weeks"],
        "diagnoses": ["E11.9 Type 2 diabetes", "", "Consent on file"],
        "medications": {"primary": "Metformin 500 mg twice daily"},
    }
    RefactoredSummariser._merge_payload(aggregated, payload)
    assert aggregated["provider_seen"][0].startswith("Dr. Gomez")
    assert aggregated["reason_for_visit"] == ["Follow-up requested"]
    assert aggregated["clinical_findings"] == ["BP 120/80 noted"]
    assert aggregated["treatment_plan"][0].startswith("Return to clinic")
    assert aggregated["diagnoses"] == ["E11.9 Type 2 diabetes"]
    assert aggregated["medications"] == ["Metformin 500 mg twice daily"]


@pytest.mark.asyncio
async def test_summarise_async_matches_sync():
    backend = StubBackend(
        responses={
            1: {
                "overview": "Sync/async test.",
                "reason_for_visit": ["Patient followed for hypertension."],
                "clinical_findings": ["Blood pressure 128/78 mmHg."],
                "treatment_plan": ["Continue lisinopril 20 mg daily."],
            }
        }
    )
    summariser = RefactoredSummariser(backend=backend, min_summary_chars=64)
    sync_result = summariser.summarise("Vitals stable.")
    async_result = await summariser.summarise_async("Vitals stable.")
    assert sync_result["Medical Summary"] == async_result["Medical Summary"]


def test_provider_names_extracted_from_source_text() -> None:
    summariser = RefactoredSummariser(backend=ProviderEmptyBackend())
    sample_text = (
        "Dr. Alice Nguyen evaluated the patient and discussed the treatment plan. "
        "Follow-up with Doctor Brian Ortiz, MD was arranged to review imaging findings. "
    ) * 20
    summary = summariser.summarise(sample_text)
    providers_blob = summary.get("_providers_list", "")
    assert "Alice Nguyen" in providers_blob or "Brian Ortiz" in providers_blob
    provider_seen_lines = summary.get("provider_seen", [])
    assert any("Alice Nguyen" in line or "Brian Ortiz" in line for line in provider_seen_lines)


def test_summarise_raises_when_chunker_returns_empty(monkeypatch):
    backend = StubBackend(responses={1: {"overview": "Chunk missing"}})

    class _EmptyChunker:
        def __init__(self, **_: Any) -> None:
            pass

        def split(self, _: str) -> list[Any]:
            return []

    monkeypatch.setattr(
        "src.services.summarization.controller.SlidingWindowChunker", _EmptyChunker
    )
    summariser = RefactoredSummariser(backend=backend)
    with pytest.raises(SummarizationError):
        summariser.summarise("content needed")


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
