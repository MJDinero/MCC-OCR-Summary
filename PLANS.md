# PLANS.md — MCC-OCR-Summary Refactor Master Plan
Status: Draft
Owner: Codex + human reviewer
Workflow assumption: feature branch + PR
Deployment assumption for v1: keep Cloud Run as the primary runtime target; do not
decompose into more services until safety and reproducibility are achieved.
## Refactor objective
Bring the repository and one clean staging GCP project to a state that is:
- privacy-safe
- fail-closed
- reproducible
- test-gated
- least-privilege
- documented well enough for repeated agent execution
## Execution rule
Always work one item at a time in this order:
1. verify
2. plan
3. patch
4. validate
5. update evidence
6. decide next item
Never jump ahead to architecture cleanup while P0/P1 remain open.
## Phase 0 — Repo + GCP read-only audit
### Goal
Establish a trusted baseline before writing changes.
### Deliverables
- `docs/CURRENT_STATE.md` updated with:
- branch
- commit
- task id
- target GCP project/region (if known)
- verified facts
- unknowns
- A short mismatch list between repo config and live cloud state
### Exit criteria

- Repo facts verified
- Cloud unknowns explicit
- No cloud writes performed
## Phase 1 — Privacy/logging hardening
### Goal
Eliminate raw sensitive error strings and unsafe DLQ/logging behavior.
### Primary surfaces
- storage failure handling
- logging adapters / redaction paths
### Exit criteria
- No raw error strings in logs or DLQ payloads
- Tests prove redaction on failure paths
## Phase 2 — Fail-closed orchestration/state
### Goal
Disallow silent success semantics in non-local environments.
### Primary surfaces
- pipeline state backend selection
- workflow launcher selection
- startup validation
### Exit criteria
- Non-local environments refuse noop workflow launch and in-memory state
- Tests prove refusal behavior
## Phase 3 — Deploy hardening and config extraction
### Goal
Make the automated deploy path match intended security posture and remove environment
drift.
### Primary surfaces
- `cloudbuild.yaml`
- env/config contract
- staging deployment documentation
### Exit criteria
- Deploy path is explicit, private, and reproducible
- Environment-specific values are documented and minimized
- HUMAN MUST RUN steps are isolated in the GCP plan

## Phase 4 — CI truthfulness
### Goal
Make green checks mean something.
### Primary surfaces
- coverage scope
- mypy strictness
- test selection
- smoke path
### Exit criteria
- `pytest --cov=src` is the default measurement basis
- touched behavior has direct tests
- repo-wide checks are reproducible locally and in CI
## Phase 5 — Observability and IAM tightening
### Goal
Reduce blind spots and blast radius.
### Primary surfaces
- metrics enablement/alignment
- alerting assumptions
- IAM breadth
- service account usage
### Exit criteria
- Metrics/documentation/deploy settings are aligned
- Broad grants reviewed and narrowed where possible
- Remaining IAM writes are documented as HUMAN MUST RUN
## Phase 6 — Optional architecture consolidation
### Goal
Clean up duplicate or legacy paths only after the system is safe and truthful.
### Primary surfaces
- dual summariser paths
- drifted docs
- dead code / compatibility shims
### Exit criteria
- Single preferred implementation path per responsibility
- No regression in tests or runtime behavior
## Progress log format

For each completed item, record:
- phase
- objective
- files changed
- commands run
- result
- blockers
- rollback note

## Progress log
- phase: `Phase 0 + Phase 1 (first repo-local P0 item)`
- objective: `Complete repo audit baseline, defer blocked cloud audit, and harden storage failure redaction for logs + DLQ payload`
- files changed:
- `src/services/storage_service.py`
- `tests/test_storage_service_pipeline.py`
- `docs/CURRENT_STATE.md`
- `PLANS.md`
- commands run:
- `git fetch origin`
- `git checkout main`
- `git merge --ff-only origin/main`
- `git checkout -b codex/feat/phase0-audit-p0-redaction`
- repo audit/read commands for required docs and hotspots
- `.venv/bin/python -m pytest tests/test_storage_service_pipeline.py -q --no-cov`
- `.venv/bin/python -m ruff check src tests`
- `.venv/bin/python -m mypy --strict src`
- `.venv/bin/python -m pytest --cov=src --cov-report=term-missing`
- result: `Done for this task. Redaction is explicit and tested on storage failure path; required local validation passed.`
- blockers: `Read-only GCP audit deferred (target project/region not human-confirmed for task; credential context not confirmed).`
- rollback note: `Revert commit for this branch to restore prior failure logging/DLQ behavior if needed.`
## Validation
- Each phase must end with concrete command output and updated evidence.
- No phase is complete until tests relevant to the touched surface pass.
- `docs/CURRENT_STATE.md` must be refreshed whenever verified facts change.
## Failure Modes
- If target GCP environment is unclear, stop after Phase 0 and request a human decision.
- If a phase requires destructive cloud changes, convert that step into HUMAN MUST RUN and
continue only on repo-safe work.
- If two iterations fail to improve the same exit criterion, stop and escalate.
