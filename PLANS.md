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

## Autonomous phase queue ledger (2026-03-03 deps-and-summary-quality-pass)
- Phase 0: `Done with blocker` (branch bootstrap completed; `git fetch origin` failed twice with GitHub HTTP 500, then local `main` and `origin/main` were already aligned at `b1a020c`)
- Phase 1: `Done with blocker` (safe dependency pin patch prepared, but `.venv` sync/install blocked by DNS/PyPI resolution after two focused attempts)
- Phase 2: `Done` (active summarization root-cause fixes implemented with characterization tests)
- Phase 3: `Done` (optional cleanup limited to inventory; no broad prune/delete)
- Phase 4: `Done` (full validation matrix rerun; strict gates green except known dependency backlog tools)
- Phase 5: `Blocked` (local commit complete, but push/PR blocked by repeated GitHub HTTP 500 on remote operations)

### Rebuilt remaining-work queue for this pass
1. `dependency issues safe to fix now`
- `python-multipart` `0.0.20 -> >=0.0.22` (GHSA-wp53-j4wj-2cfg)
- `urllib3` `2.5.0 -> >=2.6.3` (multiple GHSA advisories)
- `pypdf` installed in `.venv` is `4.2.0` despite `requirements.txt` pin drift; upgrade/install to patched `6.7.x`
- Ensure direct declaration for `reportlab` because `src/services/pdf_writer.py` imports it directly (`deptry` DEP003)
2. `dependency issues likely risky / defer unless validation stays clean`
- `protobuf` `4.25.8` advisory fix requires major line jump (`5.29.6+`); treat as higher-risk runtime change due Google client compatibility surface
- `orjson` `3.11.3` has advisory without listed fixed release; consider removal only if confirmed unused and non-transitive
3. `summarization / formatting hotspots to investigate`
- Active runtime path uses `src/services/summariser_refactored.py` from `src/main.py` and API/process path uses `SummaryContract -> PDFWriterRefactored`.
- `Resolved`: OpenAI Responses call now uses SDK-supported `text.format` JSON schema path (not deprecated `response_format`).
- `Resolved`: provider/signature extraction now reads raw OCR text instead of whitespace-flattened text.
- `Resolved`: short-summary padding now adds structured supplemental context and deterministic guidance instead of raw repeated filler.
- `Resolved`: patient-facing provider section no longer includes runtime chunk-count telemetry.
4. `optional cleanup inventory candidates (no broad deletion)`
- Massive `deptry` DEP002 list likely mixes dev tooling + transitive pins; only high-confidence changes in touched surface should be applied this pass.

## Autonomous phase queue ledger (2026-03-02 phase6-reaudit-deps-closeout)
- Phase 0: `Done` (branch state verified; archive commit `683624b` reviewed and kept)
- Phase 1: `Done` (repo-root `.venv` verified at `/Users/quantanalytics/dev/MCC-OCR-Summary/.venv/bin/python`)
- Phase 2: `Done with blockers` (`deptry` install blocked by DNS/PyPI resolution; `pip-audit` blocked by DNS to `pypi.org`)
- Phase 3: `Done with blocker` (gcloud auth/config verified; ADC token minting blocked by DNS to `oauth2.googleapis.com`)
- Phase 4: `Done` (full read-only GCP inventory command set executed for `quantify-agent` / `us-central1`)
- Phase 5: `Done` (live-vs-repo drift classified with concrete mismatches)
- Phase 6: `Done` (minimal repo-local reconciliation in ledgers only)
- Phase 7: `Done` (docs-only integrity checks performed)
- Phase 8: `Done` (`PLANS.md` and `docs/CURRENT_STATE.md` updated with evidence)

## Historical autonomous phase queue ledger (2026-03-02 final-autonomous-pass)
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
- phase: `Phase 0 + Phase 1 + Phase 2 + Phase 3 + Phase 4 (deps-and-summary-quality-pass)`
- objective: `Revalidate baseline, apply safe dependency pin updates where possible, fix active large-PDF summarization quality regressions, and rerun strict quality gates`
- files changed:
- `requirements.txt`
- `src/services/summariser_refactored.py`
- `src/services/summarization/formatter.py`
- `tests/test_summariser_refactored.py`
- `docs/CURRENT_STATE.md`
- `PLANS.md`
- commands run:
- branch bootstrap + baseline commands (`git status`, `git log`, full validation matrix, `bandit`, `deptry`, `pip-audit`)
- dependency attempt commands:
  - `.venv/bin/python -m pip install -r requirements.txt` (attempted twice)
- summarization-targeted validation:
  - `.venv/bin/python -m pytest tests/test_summariser_refactored.py -q --no-cov`
  - `.venv/bin/python -m pytest tests/test_summary_contract_integration.py -q --no-cov`
- final supervisor pass commands:
  - `.venv/bin/python -m ruff check src tests`
  - `.venv/bin/python -m mypy --strict src`
  - `.venv/bin/python -m pytest --cov=src --cov-branch --cov-report=term-missing`
  - `.venv/bin/python -m pylint --jobs=1 --score=y --fail-under=9.5 <important+changed+hotspot files>`
  - `.venv/bin/python -m pylint --jobs=1 --score=y --fail-under=9.5 <module-by-module file score capture>`
  - `.venv/bin/bandit -r src`
  - `.venv/bin/python -m deptry .`
  - `.venv/bin/pip-audit --local`
- phase 5 commands:
  - `git add PLANS.md docs/CURRENT_STATE.md requirements.txt src/services/summariser_refactored.py src/services/summarization/formatter.py tests/test_summariser_refactored.py`
  - `git commit -m "fix: improve large-pdf summary quality and update dependency pins"`
  - `git push -u origin codex/feat/deps-and-summary-quality-pass` (attempted twice)
- result: `Done for phases 0-4 and local phase-5 commit creation. Large-PDF summarization path now uses SDK-valid structured output and improved formatting/aggregation behavior with passing tests and full repo gates.`
- blockers:
- `git fetch origin` failed twice with GitHub HTTP 500.
- `.venv` dependency sync blocked by DNS/PyPI resolution (`No matching distribution found` after repeated name-resolution failures), so vulnerability reduction could not be validated in the local environment.
- `git push -u origin codex/feat/deps-and-summary-quality-pass` failed twice with GitHub HTTP 500, blocking PR creation/merge for this pass.
- rollback note: `Revert this pass commit(s) to restore previous summariser/formatter behavior and dependency pins.`

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

- phase: `Phase 0 + Phase 1 + Phase 2 + Phase 3 + Phase 4 + Phase 5 + Phase 6 + Phase 7 + Phase 8 (phase6-reaudit-deps-closeout)`
- objective: `Verify repo-root .venv, rerun dependency tooling at repo root, complete canonical read-only GCP inventory, and reconcile live-vs-repo drift with minimal safe repo-local updates.`
- files changed:
- `docs/CURRENT_STATE.md`
- `PLANS.md`
- commands run:
- Phase 0 git checks: `git status --short --branch`, `git log --oneline -3 --decorate`, `git show --stat --name-status 683624b`, plus commit-content/reference inspection
- Phase 1 venv checks: `pwd`, `git rev-parse --show-toplevel`, `test -x .venv/bin/python`, `ls .venv/bin/python`, `.venv/bin/python --version`
- Phase 2 dependency tooling:
  - `.venv/bin/python -m pip install deptry pip-audit`
  - `.venv/bin/python -m deptry .`
  - `.venv/bin/python -m pip_audit --local`
  - `.venv/bin/pip-audit --local`
- Phase 3 auth checks:
  - `gcloud auth list --format='table(account,status)'`
  - `gcloud config list --format='text(core.project,core.account,compute.region,run.region,workflows.location)'`
  - `gcloud auth application-default print-access-token >/dev/null`
- Phase 4 read-only cloud inventory:
  - `gcloud run services describe mcc-ocr-summary --region us-central1 --project quantify-agent --format='yaml(metadata.name,status.url,spec.template.spec.serviceAccountName,spec.template.metadata.annotations,spec.template.spec.containers)'`
  - `gcloud artifacts repositories list --project quantify-agent --location us-central1 --format='table(name,format,description)'`
  - `gcloud secrets list --project quantify-agent --format='table(name,replication.policy)'`
  - `gcloud storage buckets list --project quantify-agent --format='table(name,location)'`
  - `gcloud iam service-accounts list --project quantify-agent --format='table(email,displayName,disabled)'`
  - `gcloud projects get-iam-policy quantify-agent --format='table(bindings.role)'`
  - `gcloud workflows list --location us-central1 --project quantify-agent --format='table(name,state)'`
  - `gcloud eventarc triggers list --location us-central1 --project quantify-agent --format='table(name,transport.pubsub.topic)'`
  - `gcloud pubsub topics list --project quantify-agent --format='table(name)'`
  - `gcloud kms keyrings list --location us-central1 --project quantify-agent --format='table(name)'`
  - `bq ls --project_id=quantify-agent`
  - supplemental projection: `gcloud run services describe ... --format='yaml(metadata.annotations,spec.template.metadata.annotations,spec.template.spec.containerConcurrency,spec.template.spec.timeoutSeconds,spec.template.spec.serviceAccountName,status.url)'`
- docs-only validation: `git status --short --branch`, `git diff -- docs/CURRENT_STATE.md PLANS.md`
- result: `Done for this pass. Full read-only Phase 6 cloud audit succeeded for canonical target; major config/documentation drift identified and recorded; archive commit confirmed safe to keep.`
- blockers:
  - `deptry` installation blocked by DNS/PyPI resolution (`No matching distribution found for deptry` after repeated name-resolution failures).
  - `pip-audit` blocked by DNS to `pypi.org`.
  - `gcloud auth application-default print-access-token` blocked by DNS to `oauth2.googleapis.com` (CLI read-only inventory still succeeded).
- rollback note: `Revert this pass's docs-only commit to restore prior ledger state if needed.`
## Validation
- Each phase must end with concrete command output and updated evidence.
- No phase is complete until tests relevant to the touched surface pass.
- `docs/CURRENT_STATE.md` must be refreshed whenever verified facts change.
## Failure Modes
- If target GCP environment is unclear, stop after Phase 0 and request a human decision.
- If a phase requires destructive cloud changes, convert that step into HUMAN MUST RUN and
continue only on repo-safe work.
- If two iterations fail to improve the same exit criterion, stop and escalate.
