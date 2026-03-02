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

## Autonomous phase queue ledger (2026-03-02 final-autonomous-pass)
- Phase 0: `Done`
- Phase 1: `Done`
- Phase 2: `Done`
- Phase 3: `Done`
- Phase 4: `Done with blockers` (`deptry` unavailable; `pip-audit` blocked by DNS; Bandit high/medium resolved)
- Phase 5: `Done`
- Phase 6: `Blocked` (canonical target known, but auth token refresh fails in non-interactive read-only commands)
- Phase 7: `Done` (supervisor pass complete and PR #24 merged)
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

- phase: `Phase 1 + Phase 2 + Phase 3 + Phase 4 + Phase 5 (queue pass)`
- objective: `Stabilize PR trivy blocker repo-locally, enforce fail-closed pipeline behavior in non-local runtimes, reduce deploy config drift, and align docs/evidence`
- files changed:
- `.github/workflows/ci.yml`
- `src/services/pipeline.py`
- `tests/test_pipeline_fail_closed.py`
- `cloudbuild.yaml`
- `docs/CURRENT_STATE.md`
- `PLANS.md`
- commands run:
- `gh pr view 22 --json number,state,mergeable,mergeStateStatus,statusCheckRollup,url`
- `gh pr checks 22`
- `.venv/bin/python -m pytest tests/test_pipeline_fail_closed.py -q --no-cov`
- `.venv/bin/python -m ruff check src tests`
- `.venv/bin/python -m mypy --strict src`
- `.venv/bin/python -m pytest --cov=src --cov-report=term-missing`
- result: `Done for repo-local scope. Trivy hygiene patch and fail-closed orchestration/state controls are in place with passing local validation.`
- blockers: `Phase 1 remains pending remote confirmation until PR #22 checks rerun on pushed commit; Phase 6 remains deferred pending human-confirmed target project/region and credential context.`
- rollback note: `Revert this queue-pass commit(s) to restore prior CI credential fixture, pipeline fallback behavior, and deploy substitution defaults.`

- phase: `Phase 1 (attempt 2) + Phase 7`
- objective: `Resolve persistent trivy-report CI failure, merge green PR #22, and complete loop-back status evaluation`
- files changed:
- `.github/workflows/ci.yml`
- `docs/CURRENT_STATE.md`
- `PLANS.md`
- commands run:
- `gh run view 22597200888 --job 65470149600 --log`
- `.venv/bin/python -m ruff check src tests`
- `.venv/bin/python -m mypy --strict src`
- `.venv/bin/python -m pytest --cov=src --cov-report=term-missing`
- `gh run view 22597334447 --json conclusion,status,url,jobs,headSha,event,name`
- `gh pr view 22 --json number,state,mergeable,mergeStateStatus,statusCheckRollup,url`
- `gh pr merge 22 --merge`
- `gh pr view 22 --json number,state,mergedAt,mergeCommit,url,baseRefName,headRefName,title`
- `git checkout main`
- `git fetch origin`
- `git merge --ff-only origin/main`
- `git checkout -b codex/feat/phase6-audit-blocked`
- result: `Done. PR #22 merged with successful CI checks; phase loop-back complete.`
- blockers: `Phase 6 remains blocked/deferred until human confirms canonical staging PROJECT_ID and REGION and read-only credential context for that target.`
- rollback note: `Revert merge commit 4fae1a75aa221843371e4aad51abf80e17457556 on main if this queue pass must be rolled back.`

- phase: `Final autonomous pass — Phase 0/1/2/3/4/5/6`
- objective: `Rebuild remaining-work queue, harden repo-local safety/coverage, enforce per-file pylint gate, and attempt canonical read-only GCP audit`
- files changed:
- `src/services/drive_client.py`
- `src/runtime_server.py`
- `tests/test_summary_contract_module.py`
- `tests/test_summarization_text_utils.py`
- `tests/test_pipeline_failures.py`
- `docs/audit/archive/ChatGPT-5.2-Pro-Audit-2026-02-28.docx`
- `docs/CURRENT_STATE.md`
- `PLANS.md`
- commands run:
- branch/bootstrap commands (`git fetch origin`, `git checkout main`, `git merge --ff-only origin/main`, `git checkout -b codex/feat/final-autonomous-pass`)
- `.venv/bin/python -m ruff check src tests`
- `.venv/bin/python -m mypy --strict src`
- `.venv/bin/python -m pytest --cov=src --cov-branch --cov-report=term-missing`
- `.venv/bin/python -m pylint --jobs=1 --score=y --fail-under=9.5 <per-file targets>`
- `.venv/bin/bandit -r src`
- `.venv/bin/python -m deptry .`
- `.venv/bin/pip-audit --local`
- read-only cloud audit commands for `quantify-agent` / `us-central1` (`gcloud config/auth/run/artifacts/secrets/storage/iam/projects/workflows/pubsub/eventarc/kms`, `bq ls`)
- result: `Repo-local safety and test confidence improved; branch-coverage gate now passes at 94.91%; required important-file pylint scores pass.`
- blockers: `Phase 6 blocked by non-interactive gcloud reauthentication failure; deptry/pip-audit blocked by missing module + DNS/network access to PyPI.`
- rollback note: `Revert final-autonomous-pass commit(s) to restore prior runtime_server/drive_client behavior and pre-pass test suite state.`

- phase: `Final autonomous pass closeout`
- objective: `Complete supervisor pass packaging after PR #24 merge and mark final phase states`
- files changed:
- `PLANS.md`
- `docs/CURRENT_STATE.md`
- commands run:
- `gh pr view 24 --json number,state,mergedAt,mergeCommit,url,baseRefName,headRefName,title`
- `git checkout main`
- `git fetch origin`
- `git merge --ff-only origin/main`
- `git checkout -b codex/feat/final-autonomous-pass-closeout`
- result: `Done. Phase 7 marked complete; only explicit blocked items remain (Phase 6 auth + deptry/pip-audit environment blockers).`
- blockers: `No additional unblocked repo-local tasks remain after PR #24 merge.`
- rollback note: `Revert closeout docs commit if ledger status needs correction.`
## Validation
- Each phase must end with concrete command output and updated evidence.
- No phase is complete until tests relevant to the touched surface pass.
- `docs/CURRENT_STATE.md` must be refreshed whenever verified facts change.
## Failure Modes
- If target GCP environment is unclear, stop after Phase 0 and request a human decision.
- If a phase requires destructive cloud changes, convert that step into HUMAN MUST RUN and
continue only on repo-safe work.
- If two iterations fail to improve the same exit criterion, stop and escalate.
