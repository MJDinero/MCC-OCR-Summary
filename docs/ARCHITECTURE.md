# docs/ARCHITECTURE.md — Current Runtime, Boundaries, and Invariants
## Purpose
Give Codex a repo-specific mental model that is explicit about current reality versus target state.
## Current runtime model
Assume the current implementation is primarily a single FastAPI service until the active branch
proves otherwise.
### Primary app surface
- `src/main.py`
### Primary route groups
- `/ingest`
- `/process`
- `/process/drive/poll` (Drive intake bridge that mirrors to `INTAKE_GCS_BUCKET` for Eventarc `/ingest`)
- health endpoints
### Primary deploy artifacts
- `cloudbuild.yaml`
- `pipeline.yaml`
## Current architectural stance
- Keep the system as one deployable service while safety, testability, and reproducibility are
being fixed.
- Do not split into multiple services early.
- Treat asynchronous/cloud integrations as supporting infrastructure, not a reason to re-platform
the runtime immediately.
## Core internal boundaries
### API / ingress
- request parsing
- routing
- startup validation
- request/auth context
### Pipeline state / orchestration
- job state tracking
- workflow launch semantics
- fail-closed rules
### OCR

- PDF to extracted text / page content
### Summarisation
- chunk or document summarisation
- contract shaping
- quality checks
### Storage
- persistence
- DLQ/error behavior
- idempotency / write semantics
### PDF / Drive / output
- artifact generation
- external upload / output side effects
## External dependencies
- Cloud Run
- Cloud Build
- Cloud Storage
- IAM / service accounts
- Secret Manager
- Workflows / Eventarc / Pub/Sub where configured
- KMS
- BigQuery
- Document AI
- Google Drive
- OpenAI API
## Architectural invariants
These are required target truths for v1:
- No raw PHI/PII in logs or DLQ payloads
- Non-local environments fail closed on orchestration/state misconfiguration
- Automated deploy path is explicit and private by default
- Current-state docs are updated when verified facts change
- CI signals reflect the code that matters
- No secret-like fixtures are committed when they can be generated at runtime
## Threat model summary
### Highest-risk classes
- PHI leakage in failure paths
- false-success orchestration semantics
- public or weakly controlled service exposure
- broad IAM grants

- drift between repo config and live cloud config
- stale docs causing incorrect agent behavior
## Refactor stance
- Safety first
- Truthful tests second
- Deploy hardening third
- Structural cleanup last
## Validation
- When this file changes, confirm the change is supported by current repo or verified cloud
evidence.
- Verify that any new invariant has an associated validation path in `docs/TESTING.md` or
`docs/GCP_REFACTOR_PLAN.md`.
## Failure Modes
- If current and target architecture are conflated, split them back into separate sections.
- If a dependency is undocumented, add it here before relying on it in plans.
- If the agent proposes service decomposition before P0/P1 are complete, reject that change.
