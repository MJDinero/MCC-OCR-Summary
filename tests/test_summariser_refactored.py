from __future__ import annotations

from dataclasses import dataclass
import json
import sys
from typing import Dict, Any
from types import SimpleNamespace

import pytest

from src.errors import SummarizationError
from src.services.metrics_summariser import reset_state as reset_metrics_state, snapshot_state as snapshot_metrics_state
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
        result.setdefault("overview", f"Chunk {chunk_index} covers {len(chunk_text.split())} words.")
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

    summariser = RefactoredSummariser(backend=backend, target_chars=300, max_chars=420, overlap_chars=60)
    summary = summariser.summarise(text, doc_metadata={"facility": "MCC Neurology"})
    medical_summary = summary["Medical Summary"]

    assert "Intro Overview:" in medical_summary
    assert "Key Points:" in medical_summary
    assert "Detailed Findings:" in medical_summary
    assert "Care Plan & Follow-Up:" in medical_summary
    assert len(medical_summary) >= summariser.min_summary_chars

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


def test_short_input_fallback_uses_structured_summaries_only() -> None:
    class ShortFallbackBackend(ChunkSummaryBackend):
        def summarise_chunk(
            self,
            *,
            chunk_text: str,
            chunk_index: int,
            total_chunks: int,
            estimated_tokens: int,
        ) -> Dict[str, Any]:
            return {
                "overview": "Brief clinic touch-base captured via summarised chunk output.",
                "key_points": [
                    "Follow-up scheduled for medication review at the community clinic.",
                    "Follow-up scheduled for medication review at the community clinic.",
                ],
                "clinical_details": [
                    "Patient reported improved symptoms during the last documented seven-day period.",
                    "Patient reported improved symptoms during the last documented seven-day period.",
                ],
                "care_plan": [
                    "Continue current therapy plan and escalate if new red-flag symptoms are noted."
                ],
                "diagnoses": ["Essential hypertension noted as stable in the review."],
                "providers": ["Dr. Sample Provider"],
                "medications": ["Lisinopril 10 mg daily therapy."],
                "schema_version": OpenAIResponsesBackend.CHUNK_SCHEMA_VERSION,
            }

    summariser = RefactoredSummariser(backend=ShortFallbackBackend(), min_summary_chars=900)
    raw_text = "raw chunk injection sentinel with FAX header.\n" * 5
    summary = summariser.summarise(raw_text)
    medical_summary = summary["Medical Summary"]

    assert len(medical_summary) >= summariser.min_summary_chars
    assert "raw chunk injection" not in medical_summary.lower()
    assert "fax" not in medical_summary.lower()

    bullet_lines = [line for line in medical_summary.splitlines() if line.strip().startswith("- ")]
    lowered_bullets = [line.lower() for line in bullet_lines]
    assert len(lowered_bullets) == len(set(lowered_bullets))

    assert "Structured Narrative Recap:" not in medical_summary
    assert "Summary Notes:" not in medical_summary


def test_low_overlap_lines_flagged_and_removed() -> None:
    text = (
        "Patient reports persistent right knee pain with swelling after running. "
        "MRI confirms medial meniscus tear with mild effusion. "
        "Dr. Rivera recommends rest, NSAIDs, and physical therapy."
    )

    backend = StubBackend(
        responses={
            1: {
                "key_points": [
                    "Extraterrestrial infiltration observed in cranial vault.",
                    "Right knee pain limits activity levels.",
                ],
                "clinical_details": [
                    "MRI confirms medial meniscus tear with mild effusion.",
                ],
                "care_plan": [
                    "Rest, NSAIDs, and physical therapy recommended by Dr. Rivera.",
                ],
                "diagnoses": [
                    "Medial meniscus tear with joint effusion, right knee",
                ],
                "providers": ["Dr. Rivera"],
                "medications": ["NSAIDs as needed"],
            }
        }
    )

    summariser = RefactoredSummariser(backend=backend, target_chars=200, max_chars=260, overlap_chars=40)
    summary = summariser.summarise(text)
    medical_summary = summary["Medical Summary"]

    assert "Extraterrestrial infiltration" not in medical_summary
    assert summary["_needs_review"] == "true"
    flagged = json.loads(summary["_low_overlap_lines"])
    assert any(entry["line"].startswith("Extraterrestrial") for entry in flagged)


def test_summariser_metrics_state_updates() -> None:
    reset_metrics_state()
    text = ("Follow-up visit for chronic knee pain and rehab exercises. " * 40)
    backend = StubBackend(
        responses={
            1: {
                "overview": "Knee rehab visit",
                "key_points": ["Patient doing well"],
                "clinical_details": ["Range of motion improved"],
                "care_plan": ["Continue PT"],
                "diagnoses": ["Patellofemoral pain"],
                "providers": ["Dr. Rehab"],
                "medications": ["NSAIDs"],
            }
        }
    )
    summariser = RefactoredSummariser(backend=backend, target_chars=200, max_chars=260, overlap_chars=40)
    summariser.summarise(text)
    state = snapshot_metrics_state()
    assert state["chunks_total"] >= 1


def test_summary_uses_ascii_bullets_and_omits_empty_lists() -> None:
    class MinimalBackend(ChunkSummaryBackend):
        def summarise_chunk(
            self,
            *,
            chunk_text: str,
            chunk_index: int,
            total_chunks: int,
            estimated_tokens: int,
        ) -> Dict[str, Any]:
            return {
                "overview": "Clinic follow-up visit focused on hypertension control.",
                "key_points": [
                    "• Blood pressure improving with current regimen.",
                    "Medication adherence reinforced during visit.",
                    "HCPCS/ CODE PROCEDURE DESCRIPTION 0250 BUPIVACAINE 0.5% PF SOL",
                ],
                "clinical_details": [],
                "care_plan": [
                    "— Continue lisinopril 20 mg daily.",
                    "Arrange labs in two weeks to review electrolytes.",
                    "Guarantor Name: ANTHONY WILLIAMS SSN: XXX-XX-1234",
                ],
                "diagnoses": [],
                "providers": [],
                "medications": [],
            }

    summariser = RefactoredSummariser(backend=MinimalBackend(), min_summary_chars=520)
    text = "Patient attended clinic visit for ongoing hypertension management." * 10
    summary = summariser.summarise(text)
    medical_summary = summary["Medical Summary"]

    assert "Diagnoses:" not in medical_summary
    assert "Providers:" not in medical_summary
    assert "Medications / Prescriptions:" not in medical_summary

    bullet_lines = [line for line in medical_summary.splitlines() if line.strip().startswith("-")]
    assert bullet_lines, "Expected bullet lines in structured sections"
    for line in bullet_lines:
        stripped = line.strip()
        assert stripped.startswith("- ")
        assert "•" not in stripped
        assert "—" not in stripped
        assert "HCPCS" not in stripped
        assert "Guarantor" not in stripped

    assert "Care Plan & Follow-Up:" in medical_summary


def test_summary_dedup_filters_and_optional_sections() -> None:
    class DedupBackend(ChunkSummaryBackend):
        def summarise_chunk(
            self,
            *,
            chunk_text: str,
            chunk_index: int,
            total_chunks: int,
            estimated_tokens: int,
        ) -> Dict[str, Any]:
            return {
                "overview": "Follow-up visit for knee injury with improving function.",
                "key_points": [
                    "Continue physical therapy for knee pain.",
                    "MRI follow-up confirms no structural complications.",
                ],
                "clinical_details": [
                    "Continue physical therapy for knee pain.",
                    "MRI of the left knee shows resolving effusion.",
                    "No marrow contusion identified on imaging.",
                ],
                "care_plan": [
                    "Continue physical therapy for knee pain.",
                    "Review MRI results at next visit.",
                ],
                "diagnoses": [
                    "No fracture identified.",
                    "No marrow contusion.",
                    "Patellofemoral pain syndrome.",
                ],
                "providers": ["Dr. Jamie Lee MD", "Sunrise Imaging Center"],
                "medications": [
                    "Ibuprofen 400mg as needed for discomfort.",
                    "I will continue my medications exactly as instructed.",
                ],
            }

    summariser = RefactoredSummariser(
        backend=DedupBackend(),
        min_summary_chars=200,
        target_chars=400,
        max_chars=600,
        overlap_chars=50,
    )
    summary = summariser.summarise(
        "Short clinical note describing imaging results and therapy plan.",
        doc_metadata={"facility": "MCC Ortho", "billing": "Not provided", "legal_notes": " "},
    )

    medical_summary = summary["Medical Summary"]
    assert "Document processed in" not in medical_summary
    assert "chunk(s)" not in medical_summary.lower()
    assert "Imaging Findings:" in medical_summary
    assert "Diagnoses:" not in medical_summary
    assert "Providers:" not in medical_summary
    assert "Medications / Prescriptions:" not in medical_summary
    assert "Summary Notes:" not in medical_summary

    lines = medical_summary.splitlines()
    headers = [
        "Intro Overview:",
        "Key Points:",
        "Detailed Findings:",
        "Care Plan & Follow-Up:",
    ]
    sections: Dict[str, list[str]] = {header: [] for header in headers}
    current = None
    for line in lines:
        if line in headers:
            current = line
            continue
        if current:
            sections[current].append(line)

    key_point_lines = [ln for ln in sections["Key Points:"] if ln.startswith("- ")]
    detail_lines = sections["Detailed Findings:"]
    care_plan_lines = [ln for ln in sections["Care Plan & Follow-Up:"] if ln.startswith("- ")]

    dedup_line = "- Continue physical therapy for knee pain."
    assert key_point_lines.count(dedup_line) == 1
    assert dedup_line not in detail_lines
    assert dedup_line not in care_plan_lines

    imaging_block_index = detail_lines.index("Imaging Findings:")
    assert detail_lines[imaging_block_index + 1].startswith("- MRI of the left knee")

    meds = [ln for ln in summary["_medications_list"].splitlines() if ln.strip()]
    assert meds == ["Ibuprofen 400 mg as needed for discomfort."]
    providers = [ln for ln in summary["_providers_list"].splitlines() if ln.strip()]
    assert providers == ["Dr. Jamie Lee MD"]
    diagnoses = [ln for ln in summary["_diagnoses_list"].splitlines() if ln.strip()]
    assert diagnoses[0] == "No acute fracture"
    assert diagnoses.count("No acute fracture") == 1
    assert "Patellofemoral pain syndrome." in diagnoses

    assert "Billing Highlights" not in summary
    assert "Legal / Notes" not in summary


def test_condensed_summary_headers_remain_tolerant() -> None:
    class VerboseBackend(ChunkSummaryBackend):
        def summarise_chunk(
            self,
            *,
            chunk_text: str,
            chunk_index: int,
            total_chunks: int,
            estimated_tokens: int,
        ) -> Dict[str, Any]:
            base_detail = "Patient follow-up narrative detailing ongoing management and monitoring obligations."
            key_points = [
                f"Key insight {i} underpins continued management strategy with emphasis on adherence."
                for i in range(1, 7)
            ]
            clinical_details = [
                f"Clinical observation {i}: {base_detail} Additional metrics documented explicitly for reviewers."
                for i in range(1, 15)
            ]
            care_plan = [
                f"Care directive {i} incorporates scheduling, medication reconciliation, and escalation guardrails."
                for i in range(1, 10)
            ]
            return {
                "overview": "Extended visit documentation aggregated from multiple chunk-level summaries.",
                "key_points": key_points,
                "clinical_details": clinical_details,
                "care_plan": care_plan,
                "diagnoses": ["Chronic condition managed through coordinated care."],
                "providers": ["Lead clinician signature present."],
                "medications": ["Ongoing pharmacotherapy maintained without modification."],
            }

    summariser = RefactoredSummariser(
        backend=VerboseBackend(),
        target_chars=500,
        max_chars=650,
        overlap_chars=120,
        min_summary_chars=520,
        collapse_threshold_chars=420,
    )
    text = "Comprehensive follow-up documentation with extensive narrative sections. " * 80
    summary = summariser.summarise(text)
    medical_summary = summary["Medical Summary"]

    assert len(medical_summary) >= summariser.min_summary_chars
    for header in ("Intro Overview:", "Key Points:", "Detailed Findings:", "Care Plan & Follow-Up:"):
        assert header in medical_summary
    assert "Care Plan & Follow-Up (Condensed):" not in medical_summary
    assert "Detailed Findings (Condensed):" not in medical_summary
    assert "Summary Notes:" not in medical_summary
    assert "Summary condensed from chunk-level outputs" in medical_summary
    assert "•" not in medical_summary
    assert "—" not in medical_summary


def test_summary_filters_discharge_boilerplate() -> None:
    class BoilerplateBackend(ChunkSummaryBackend):
        def summarise_chunk(
            self,
            *,
            chunk_text: str,
            chunk_index: int,
            total_chunks: int,
            estimated_tokens: int,
        ) -> Dict[str, Any]:
            return {
                "overview": "Patient presented with lumbar pain following a workplace fall.",
                "key_points": [
                    "Patient complains of persistent lumbar pain with intermittent paresthesia down the right leg.",
                    "Follow the instructions from your healthcare provider about when to resume normal activities.",
                ],
                "clinical_details": [
                    "MRI on 08/07/2024 demonstrated no compression fracture or canal stenosis.",
                    "Signs of infection such as fever or drainage were reviewed with the patient for discharge education.",
                    "Risks include bleeding, infection, or nerve injury though complications may be rare.",
                ],
                "care_plan": [
                    "Continue physical therapy twice weekly focusing on lumbar stabilization.",
                    "When to stop eating and drinking before the planned injection was discussed with the patient.",
                    "Informed consent documentation was provided to the patient for signature.",
                ],
                "diagnoses": [
                    "Lumbar strain with radicular symptoms",
                ],
                "providers": [
                    "Dr. Alicia Carter",
                ],
                "medications": [
                    "Ibuprofen 600 mg every 8 hours as needed for pain",
                ],
            }

    summariser = RefactoredSummariser(backend=BoilerplateBackend(), min_summary_chars=520)
    text = "Workplace injury leading to lumbar pain." * 40
    summary = summariser.summarise(text)
    medical_summary_lower = summary["Medical Summary"].lower()

    assert "follow the instructions" not in medical_summary_lower
    assert "when to stop eating" not in medical_summary_lower
    assert "signs of infection" not in medical_summary_lower
    assert "risks include" not in medical_summary_lower
    assert "informed consent" not in medical_summary_lower
    assert "lumbar pain" in medical_summary_lower
    assert "mri" in medical_summary_lower
    assert "physical therapy" in medical_summary_lower

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

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=lambda api_key: _FakeClient(api_key)))

    with pytest.raises(SummarizationError):
        backend.summarise_chunk(chunk_text="data", chunk_index=1, total_chunks=1, estimated_tokens=500)


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

    payload_path.write_text(json.dumps({"pages": [{"text": "Only pages"}]}), encoding="utf-8")
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

        def list_blobs(self, *_args, **_kwargs):  # pragma: no cover - not used in this scenario
            return []

    monkeypatch.setattr("google.cloud.storage.Client", lambda: _StubClient({"ocr/job/aggregate.json": blob_bytes}))

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

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=lambda api_key: _StubClient()))

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

    _cli(["--input", str(input_path), "--output", str(output_path), "--api-key", "dummy-key"])

    summary_output = json.loads(output_path.read_text(encoding="utf-8"))
    assert summary_output["Medical Summary"]
    assert "blood pressure" in summary_output["Medical Summary"].lower()
