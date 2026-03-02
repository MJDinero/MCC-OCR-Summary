# docs/CODEBASE_MAP.md — Tactical Navigation Map
## Purpose
Help Codex find the right files fast and avoid unsafe edits.
## Top-level map
- `src/` — application code
- `tests/` — automated test suite
- `scripts/` — helper scripts
- `infra/` — infrastructure-as-code and operational scripts
- `.github/` — CI / repo automation
- `docs/` — refactor/runbook docs
- `cloudbuild.yaml` — automated deploy path
- `pipeline.yaml` — manifest / deployment-related artifact
- `PLANS.md` — refactor master plan
- `PROGRESS.md` — progress log/history
- `README.md` / `REPORT.md` — historical/operator context; verify against repo truth
## Hotspots (edit carefully)
### `src/main.py`
Why it matters:
- app composition
- router mounts
- startup validation
- metrics enablement
- top-level dependency wiring
### `src/api/ingest.py`
Why it matters:
- ingestion behavior
- status route semantics
- workflow parameter passing
- auth/token surface
### `src/api/process.py`
Why it matters:
- sync processing path
- external side effects
- request/response contract
### `src/services/pipeline.py`
Why it matters:
- state backend selection

- workflow launcher semantics
- fail-open / fail-closed behavior
### `src/services/storage_service.py`
Why it matters:
- persistence success/failure logging
- DLQ payload construction
- redaction correctness
### `src/services/*summar*`
Why it matters:
- duplicate or legacy/refactored paths may exist
- contract shape and output quality depend on this layer
### `.github/workflows/ci.yml`
Why it matters:
- CI truthfulness
- secret-hygiene risk
- code review / automation behavior
### `cloudbuild.yaml`
Why it matters:
- current deploy truth
- environment drift
- security posture
- service identity
### `pipeline.yaml`
Why it matters:
- deployment/manifest expectations
- metrics/sidecar alignment
- drift with cloudbuild behavior
### `infra/iam.sh`
Why it matters:
- least-privilege review
- HUMAN MUST RUN implications
- blast radius if changed carelessly
## Do-not-touch rules
Do not change these without a scoped plan and validation:
- IAM / billing / secret workflows
- public/private access controls
- deploy-time service identity

- health/status route behavior
- storage failure-path semantics
- CI gates
## Recommended navigation order for new tasks
1. `PLANS.md`
2. `docs/CURRENT_STATE.md`
3. `src/main.py`
4. touched API/service files
5. tests for touched surface
6. deploy/config files only if needed
## Validation
- Every file touched should map back to a named plan item.
- Every risky file touched should have corresponding tests or explicit validation evidence.
- Every deploy/config edit should be reflected in `docs/CURRENT_STATE.md`.
## Failure Modes
- If a task starts from the wrong hotspot, stop and remap the task here first.
- If multiple layers are changing at once (API + deploy + IAM), split the work.
- If a file’s responsibility is unclear, document it here before making broad edits.

