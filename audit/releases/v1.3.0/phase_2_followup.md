# Phase 2 Follow-up – /ingest Duplicate Policy

## Summary
- Investigated failing assertions in `tests/test_idempotency.py` and `tests/test_pipeline_endpoints.py` expecting HTTP 412 on duplicate ingest submissions.
- Reviewed historical artefacts (README, `scripts/e2e_smoke.sh`, `pipeline.yaml`) confirming the product requirement: repeat ingests must return **412 Precondition Failed** with the existing job payload so upstream automation can detect idempotent retries.

## Remediation
- Updated `src/api/ingest.py` to return `412` for `DuplicateJobError` responses and explicitly flag `duplicate=True` in the payload.
- Retained structured logging (`ingest_duplicate`) to aid observability while aligning response semantics with documented workflows.

## Validation
- Pending: rerun full `pytest` suite (captured in Phase 3 prep) to confirm duplicate handling expectations now pass without overrides.
