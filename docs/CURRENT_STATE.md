# docs/CURRENT_STATE.md — Verified Current State Register

Last updated: 2026-03-02 13:45:54 PST
Updated by: Codex (thread: phase0-audit-p0-redaction)
Repo branch: `codex/feat/phase0-audit-p0-redaction`
Repo commit (pre-change baseline): `2fe2834290d4baf8ff6cf76c260f6fba27781d93`
Task id: `phase0-audit-p0-redaction`
Target GCP project: `UNKNOWN (not human-confirmed for this task; local default historically showed quantify-agent)`
Target region: `UNKNOWN (not human-confirmed)`
Cloud audit status: `DEFERRED (blocked: target PROJECT_ID/REGION and credential context are not explicitly confirmed for this thread)`

## Phase Queue Status (current pass)
- Phase 0: `DONE` (branch/status/PR state revalidated, docs pack re-read in required order)
- Phase 1: `TODO` (repo-local trivy remediation patch complete; awaiting PR re-run evidence)
- Phase 2: `DONE` (non-local fail-closed guards implemented for state store/workflow launcher + tests)
- Phase 3: `DONE` (deploy config drift reduced in `cloudbuild.yaml` via explicit project/region/service substitutions)
- Phase 4: `DONE` (CI security-scan hygiene improved by removing committed key-like material)
- Phase 5: `DONE` (docs/evidence synchronized in this update set)
- Phase 6: `DEFERRED` (conditional read-only GCP audit prerequisites still unmet)
- Phase 7: `TODO` (loop-back after PR/CI refresh)

## Verified facts
- Required docs were re-read in authoritative order: `AGENTS.md`, `PLANS.md`, `docs/CURRENT_STATE.md`, `docs/REFACTOR_RUNBOOK.md`, `docs/ARCHITECTURE.md`, `docs/CODEBASE_MAP.md`, `docs/TESTING.md`, `docs/GCP_REFACTOR_PLAN.md`.
- Working tree branch is still `codex/feat/phase0-audit-p0-redaction`.
- PR `#22` remains open and mergeable, with `mergeStateStatus=UNSTABLE` due to failed `trivy-report` check from run `22594728364`.
- `.github/workflows/ci.yml` previously contained committed PEM-like key material in the test credential step; that material has been removed and replaced with runtime-generated mock key content.
- `src/services/pipeline.py` now fails closed in non-local runtimes when:
  - `PIPELINE_STATE_BACKEND` resolves to `memory`
  - `PIPELINE_WORKFLOW_NAME` is missing
- New tests (`tests/test_pipeline_fail_closed.py`) prove non-local refusal and local/test allowance.
- `cloudbuild.yaml` now uses explicit substitutions for service/project/region in deploy args and env contract mapping.
- Full local validation currently passes after patches (`ruff`, `mypy --strict`, `pytest --cov=src`).

## Unknowns
- Question: Which `PROJECT_ID` and `REGION` are canonical for the staging target in this thread?
  - Why it matters: Required precondition for Phase 6 read-only cloud audit and any repo-vs-cloud drift claims.
  - How to verify: Human confirms exact `PROJECT_ID` and `REGION`.
  - Status: `Blocked`
- Question: Are valid read-only credentials active for the intended staging target?
  - Why it matters: Required to execute read-only audit commands safely.
  - How to verify: Human-confirmed target plus successful `gcloud auth list`/project alignment for that target.
  - Status: `Open`

## Blockers / Deferred items
- Phase 6 read-only GCP audit is deferred because target `PROJECT_ID`/`REGION` remain unconfirmed and credential context is not explicitly tied to a confirmed target.

## Evidence log
- Scope: `Phase 0 revalidation + Phase 1/2/3/4 implementation pass`
- Files changed in this pass:
  - `.github/workflows/ci.yml`
  - `src/services/pipeline.py`
  - `tests/test_pipeline_fail_closed.py`
  - `cloudbuild.yaml`
  - `PLANS.md`
  - `docs/CURRENT_STATE.md`
- Commands run:
  - `git rev-parse --abbrev-ref HEAD`
  - `git rev-parse HEAD`
  - `git status --short --branch`
  - `ls -l AGENTS.md PLANS.md docs/CURRENT_STATE.md docs/REFACTOR_RUNBOOK.md docs/ARCHITECTURE.md docs/CODEBASE_MAP.md docs/TESTING.md docs/GCP_REFACTOR_PLAN.md`
  - `gh pr view 22 --json number,title,state,mergeable,mergeStateStatus,headRefName,baseRefName,statusCheckRollup,url`
  - `gh pr checks 22`
  - `sed -n '1,320p' .github/workflows/ci.yml`
  - `rg -n "SERVICE_ACCOUNT_JSON|GOOGLE_APPLICATION_CREDENTIALS|from_service_account|private_key" src tests .github/workflows/ci.yml`
  - `sed -n '620,860p' src/services/pipeline.py`
  - `.venv/bin/python -m pytest tests/test_pipeline_fail_closed.py -q --no-cov`
  - `.venv/bin/python -m ruff check src tests`
  - `.venv/bin/python -m mypy --strict src`
  - `.venv/bin/python -m pytest --cov=src --cov-report=term-missing`
  - `rg -n "BEGIN PRIVATE KEY|SERVICE_ACCOUNT_JSON" .github/workflows/ci.yml src tests | head -n 40`
- Result:
  - Repo-local trivy hygiene patch applied.
  - Fail-closed non-local orchestration/state guardrails applied and validated.
  - Deploy config drift reduced via explicit Cloud Build substitutions.
  - Full local test and quality gates pass.
- Remaining risk:
  - PR-level trivy outcome remains pending until updated commit is pushed and checks rerun.
