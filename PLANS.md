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

## Autonomous phase queue ledger (2026-03-06 hardening-and-regression-prevention)
- Phase 0: `Done` (baseline synced on `main` at `6d143130825048a9f6bd82b0f0d1a53576b8e9dd` with clean status)
- Phase 1: `Done` (read-first docs + repo invariant audit + read-only cloud evidence audit)
- Phase 2A: `Done` (documentation evidence updated with latest successful synthetic run and classification)

### Latest successful synthetic proof evidence (authoritative)
- Classification: `LIVE_PIPELINE_WORKS`
- Synthetic source: Drive PDF `synthetic-proof-2026-03-06T02-48-58Z.pdf` (`drive_file_id=1vTpQwbB-mokyQ-K5k2514YDcgvTZF9kd`; non-PHI synthetic input)
- Scheduler/bridge evidence: `/process/drive/poll` returned `200` at `2026-03-06T02:49:06.668969Z`
- Ingest evidence: `/ingest` returned `202` at `2026-03-06T02:49:08.648690Z`
- Workflow evidence:
  - execution: `projects/720850296638/locations/us-central1/workflows/docai-pipeline/executions/cb500981-28e8-4d88-b4d6-ac3ea00406e3`
  - state: `SUCCEEDED`
  - start: `2026-03-06T02:49:09.296574608Z`
  - end: `2026-03-06T02:55:58.072328925Z`
  - `job_id`: `d66c7940b81549698e406d53418e8db1`
- Artifact evidence:
  - `gs://mcc-output/summaries/d66c7940b81549698e406d53418e8db1.json`
  - `gs://mcc-output/pdf/d66c7940b81549698e406d53418e8db1.pdf`

## Autonomous phase queue ledger (2026-03-05 workflow-yaml-indentation-repair)
- Phase 0: `Done` (PR #36 merged and `main` fast-forwarded to `125219442d0a974208e9acfa3d58ba3ff1b47cef`)
- Phase 1: `Done` (workflow deploy failed with parse error at `workflows/pipeline.yaml` line `193`, confirming YAML indentation drift)
- Phase 2: `Done` (normalized callback `url:` indentation across affected `http.post` blocks)
- Phase 3: `Done` (infra test strengthened to parse workflow YAML before callback URL assertions)
- Phase 4: `Done` (required local validation gates passed on indentation-fix branch)
- Phase 5: `Queued` (commit/push/PR lifecycle for indentation fix)
- Phase 6: `Queued` (merge + workflow deploy + synthetic rerun to confirm summary/PDF artifacts)

### Remaining queue after phases 0-4
1. `phase 5 PR lifecycle`
- commit indentation fix, push, open PR to `main`, merge when checks pass.
2. `phase 6 live verification`
- deploy workflow from merged `main`, upload one fresh synthetic PDF, run scheduler once, and verify `/process/drive/poll`, `/ingest`, workflow success, and summary/PDF artifacts.

## Autonomous phase queue ledger (2026-03-05 workflow-callback-path-repair)
- Phase 0: `Done` (PR #35 merged and redeployed; Cloud Run revision `mcc-ocr-summary-00382-8rr` confirmed with restored pipeline env contract)
- Phase 1: `Done` (fresh synthetic Drive upload + scheduler trigger + log/workflow evidence collected)
- Phase 2: `Done` (new first failing stage isolated: workflow callback requests `/internal/jobs/...` return `404`)
- Phase 3: `Done` (minimal workflow patch: callback URLs now target `/ingest/internal/jobs/{job_id}/events`)
- Phase 4: `Done` (infra regression guard added to assert callback URL prefix)
- Phase 5: `Done` (required local validation gates passed on callback-path branch)
- Phase 6: `Queued` (commit/push/PR lifecycle for callback-path fix)
- Phase 7: `Queued` (merge + workflow redeploy + fresh synthetic rerun to prove summary/PDF artifacts)

### Remaining queue after phases 0-5
1. `phase 6 PR lifecycle`
- commit callback-path fix, push, open PR to `main`, merge when checks pass.
2. `phase 7 live verification`
- deploy merged workflow fix, upload one fresh synthetic PDF, run scheduler once, and verify `/process/drive/poll`, `/ingest`, workflow success, and summary/PDF artifacts.

## Autonomous phase queue ledger (2026-03-05 pipeline-runtime-env-contract-repair)
- Phase 0: `Done` (PR #34 merged, `main` fast-forwarded to `df889ebad83ef93a7a37a17a69c30911c6070f4c`)
- Phase 1: `Done` (Cloud Run redeploy + workflow deploy + fresh synthetic Drive upload + scheduler run executed)
- Phase 2: `Done` (fresh workflow failure isolated: `validateConfig` missing `PIPELINE_SERVICE_BASE_URL`)
- Phase 3: `Done` (minimal deploy contract patch: restore pipeline callback/job env vars in `cloudbuild.yaml`)
- Phase 4: `Done` (infra guard extended in `tests/test_infra_manifest.py`)
- Phase 5: `Done` (required local validation gates passed on fix branch)
- Phase 6: `Queued` (commit/push/PR lifecycle for env-contract fix)
- Phase 7: `Queued` (merge + redeploy + rerun synthetic proof to confirm summary/PDF artifacts)

### Remaining queue after phases 0-5
1. `phase 6 PR lifecycle`
- commit fix branch, push, open PR to `main`, and merge when checks pass.
2. `phase 7 live verification`
- redeploy from merged `main`, upload one fresh synthetic PDF, run scheduler once, then verify `/process/drive/poll`, `/ingest`, workflow execution, and summary/PDF artifacts.

## Autonomous phase queue ledger (2026-03-05 workflow-init-contract-repair)
- Phase 0: `Done` (read-first docs completed; `main` baseline confirmed clean at `158b8b0c1fcbaace638fe9b4530e4194b18101af`)
- Phase 1: `Done` (workflow init contract audit completed; hard-required `event.*` keys enumerated from `workflows/pipeline.yaml`)
- Phase 2: `Done` (patched `src/api/ingest.py` workflow payload contract to always include workflow-init keys, including `project_id` and `gcs_uri`)
- Phase 3: `Done` (added backward-compatible callback auth support for both `X-Internal-Event-Token` and `X-Internal-Token`)
- Phase 4: `Done` (required validation gates passed: ruff, mypy strict, pytest coverage on `src`; focused pylint/bandit run captured)
- Phase 5: `Done` (commit `323d13f` pushed; PR opened: `https://github.com/MJDinero/MCC-OCR-Summary/pull/34`)
- Phase 6: `Blocked` (human-run cloud deploy + live synthetic PDF verification required)

### Remaining queue after phases 0-4
1. `phase 6 live verification boundary`
- HUMAN MUST RUN: deploy updated Cloud Run service and workflow definition, upload one fresh synthetic PDF, run scheduler once, and capture `/process/drive/poll` + `/ingest` + workflow execution + summary/PDF artifact evidence.

## Autonomous phase queue ledger (2026-03-04 cmek-default-alignment)
- Phase 0: `Done` (read-first order completed, repo baseline captured on feature branch)
- Phase 1: `Done` (selected highest-priority unresolved deploy-hardening item: eliminate stale CMEK default drift)
- Phase 2: `Done` (patched deploy truth in `cloudbuild.yaml` while preserving `_CMEK_KEY_NAME` substitution override)
- Phase 3: `Done` (updated direct operator guidance in `REPORT.md` to avoid reintroducing the deleted key path)
- Phase 4: `Done` (required scoped validation executed: `.venv/bin/python -m ruff check src tests`)
- Phase 5: `Queued` (commit/push/PR lifecycle)
- Phase 6: `Queued` (human-run cloud write checkpoint to update live env and trigger verification cycle)

### Remaining queue after phases 0-4
1. `phase 5 PR lifecycle`
- commit, push, open PR to `main`, and request merge approval.
2. `phase 6 runtime verification`
- HUMAN MUST RUN: update Cloud Run `CMEK_KEY_NAME`, trigger scheduler once, and capture logs/workflow/storage proof.

## Autonomous phase queue ledger (2026-03-04 drive-poll-ingress-remediation)
- Phase 0: `Done` (read-first order completed; branch/commit/task baseline captured; required pre-change validation run)
- Phase 1: `Done` (ingress architecture audit confirmed authoritative downstream path is GCS finalize -> Eventarc -> `/ingest`)
- Phase 2: `Done` (selected bounded remediation B: add repo-supported Drive poll bridge endpoint to feed intake bucket)
- Phase 3: `Done` (implemented `POST /process/drive/poll` + idempotent Drive->GCS mirror helper + tests/docs updates)
- Phase 4: `Done` (required validation matrix executed; gates pass with low-only Bandit findings)
- Phase 5: `Queued` (commit/push/PR/merge lifecycle)
- Phase 6: `Queued` (human-run scheduler/deploy verification commands)

### Remaining queue after phases 0-4
1. `phase 5 PR lifecycle`
- commit, push, open PR to `main`, watch checks, merge when green.
2. `phase 6 runtime alignment`
- HUMAN MUST RUN cloud commands to update scheduler target behavior expectations and redeploy route fix.
3. `phase 6 live proof`
- HUMAN MUST RUN proof that Drive upload produces intake object, `/ingest` call, workflow execution, and downstream artifact.

## Autonomous phase queue ledger (2026-03-03 final-hardening-and-regression-orchestration)
- Phase 0: `Done` (branch/log baseline captured and full validation matrix rerun)
- Phase 1: `Done` (formalized intentional `deptry` exceptions and retired stale lock workflow)
- Phase 2: `Done` (live-regression runbook and helper upgraded with deterministic scoring/evidence outputs)
- Phase 3: `Deferred` (no new summarization defect identified from updated regression evidence tooling)
- Phase 4: `Done` (targeted cleanup completed: stale `requirements.lock` retired; no broad deletion)
- Phase 5: `Done` (strict supervisor rerun complete; required gates pass)
- Phase 6: `Queued` (commit/push/PR/merge lifecycle)

### Remaining queue after phases 0-5
1. `phase 6 PR lifecycle`
- commit, push, open PR to `main`, watch checks, and merge when green.
2. `human-run live validation boundary`
- real large-PDF live execution remains HUMAN MUST RUN; this pass only packages deterministic repo-local tooling/docs for that flow.

## Autonomous phase queue ledger (2026-03-03 dependency-hardening-and-live-regression-prep)
- Phase 0: `Done` (branch/log baseline and full validation matrix captured; remaining-work queue rebuilt)
- Phase 1: `Done` (dependency hardening completed; `pip-audit` is now clean locally)
- Phase 2: `Done` (large-PDF regression preparation delivered via tests + runbook/helper)
- Phase 3: `Deferred` (no additional code-level summarization fix required after new evidence)
- Phase 4: `Done` (optional cleanup inventory captured only; no broad deletion)
- Phase 5: `Done` (full supervisor validation rerun with required per-file pylint set)
- Phase 6: `Queued` (commit/push/PR/merge lifecycle pending)

### Rebuilt remaining-work queue for this pass
1. `dependency issues fixed in this pass`
- `protobuf` `4.25.8 -> 5.29.6`
- `google-cloud-documentai` `2.23.0 -> 3.10.0` (compatibility unlock for protobuf remediation)
- `pillow` `12.0.0 -> 12.1.1`
- removed unused vulnerable `orjson` from runtime dependency surface
- `requirements.txt` reduced to direct runtime dependencies
- `requirements-dev.txt` now carries test/tooling dependencies
- `deptry` reduced from `82` -> `2` DEP002 findings
2. `dependency issues deferred (intentional)`
- `deptry` residual DEP002:
  - `pillow` (pinned security-sensitive transitive runtime dependency via `reportlab`)
  - `python-multipart` (runtime-required multipart parser for FastAPI upload path)
3. `summarization/live-regression prep completed`
- active runtime path reconfirmed: `src/api/process.py` -> `src/services/summariser_refactored.py` -> `src/services/summarization/formatter.py`
- new large OCR-like characterization test added (`tests/test_summariser_refactored.py`)
- new formatter structure tests added (`tests/test_summary_formatter.py`)
- human-run live regression runbook and helper added:
  - `docs/LIVE_REGRESSION_LARGE_PDF.md`
  - `scripts/verify_live_regression.py`
4. `optional cleanup inventory (no broad purge)`
- potential follow-up: codify `deptry` intentional runtime/transitive ignores
- potential follow-up: reconcile or retire stale `requirements.lock` workflow

## Autonomous phase queue ledger (2026-03-03 deps-and-summary-quality-pass continuation)
- Phase 0: `Done` (verified local branch preservation at `208ba3a` with clean worktree and expected unique commit stack)
- Phase 1: `Done` (branch push recovered successfully; PR `#28` opened and corrected)
- Phase 2: `Done` (repo `.venv` synced successfully; dependency posture improved)
- Phase 3: `Done` (additional summarization-quality patch and regression tests completed)
- Phase 4: `Done` (full supervisor validation rerun; strict gates pass)
- Phase 5: `Queued` (awaiting latest continuation commit push, PR check watch, and merge decision)

### Rebuilt remaining-work queue for this continuation
1. `dependency issues fixed in this pass`
- `python-multipart` `0.0.20 -> 0.0.22`
- `urllib3` `2.5.0 -> 2.6.3`
- `pypdf` `.venv` drift corrected to `6.7.4`
- direct `reportlab` declaration already present; DEP003 for `pdf_writer.py` no longer present
- local tooling vuln reductions: `pip` upgraded to `26.0.1`, `filelock` installed at patched `3.20.3`
2. `dependency issues deferred (higher-risk or unresolved fix path)`
- `protobuf` `4.25.8` advisory fix requires major jump (`5.29.6+` / `6.33.5`) across Google client surface
- `orjson` `3.11.3` advisory has no listed fixed version
- `pillow` `12.0.0` advisory fix (`12.1.1`) is transitive under `reportlab` and needs scoped compatibility validation
- `deptry` backlog remains large (`82` DEP002 items) and needs a dedicated prune/split pass
3. `summarization / formatting hotspots`
- Active runtime path remains `src/services/summariser_refactored.py` via `src/main.py`.
- `Resolved`: overview selection no longer drops useful content solely because `"patient"` token is absent.
- `Resolved`: key-point/detail/plan fallback now preserves valid lines when strict keyword filters over-prune.
- `Resolved`: new tests enforce non-lossy overview/detail/plan behavior for short synthetic OCR content.

## Autonomous phase queue ledger (2026-03-03 config-align-live-runtime)
- Phase 0: `Done` (read-only runtime verification rerun for `quantify-agent` / `us-central1`, including Cloud Run IAM policy)
- Phase 1: `Done` (live-vs-repo alignment matrix built and classified)
- Phase 2: `Done` (`cloudbuild.yaml` + `README.md` aligned to approved live runtime values)
- Phase 3: `Done` (`pipeline.yaml` explicitly marked as legacy/non-authoritative while preserved for tests/reference)
- Phase 4: `Done` (`reportlab` direct dependency metadata corrected; `pip-audit` inventory captured and prioritized)
- Phase 5: `Done` (required validation matrix passed: ruff, mypy strict, pytest with branch coverage)
- Phase 6: `Done` (strict self-review complete; no cloud writes, narrow scope maintained)
- Phase 7: `Blocked` (awaiting post-update commit/push/PR lifecycle)

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
- phase: `Phase 0 + Phase 1 + Phase 2 + Phase 3 + Phase 4 + Phase 5 (final-hardening-and-regression-orchestration)`
- objective: `Formalize residual dependency policy, retire stale lock workflow drift, strengthen human-run live-regression evidence tooling, and rerun strict supervisor gates.`
- files changed:
- `pyproject.toml`
- `requirements.txt`
- `README.md`
- `.github/workflows/ci.yml`
- `docs/DEPENDENCY_POLICY.md`
- `docs/LIVE_REGRESSION_LARGE_PDF.md`
- `docs/LIVE_REGRESSION_EVIDENCE_TEMPLATE.md`
- `docs/LIVE_REGRESSION_EXPECTED_IDS.example.txt`
- `docs/TESTING.md`
- `scripts/verify_live_regression.py`
- `tests/test_verify_live_regression_script.py`
- `PLANS.md`
- `docs/CURRENT_STATE.md`
- commands run:
- Phase 0 baseline:
  - `git status --short --branch`
  - `git log --oneline -5 --decorate`
  - `.venv/bin/python -m ruff check src tests`
  - `.venv/bin/python -m mypy --strict src`
  - `.venv/bin/python -m pytest --cov=src --cov-branch --cov-report=term-missing`
  - per-file pylint important set (`--persistent=n`, `--fail-under=9.5`)
  - `.venv/bin/bandit -r src`
  - `.venv/bin/python -m deptry .`
  - `.venv/bin/pip-audit --local`
- Phase 1 dependency policy + workflow:
  - `.venv/bin/python -m deptry .`
  - `.venv/bin/pip-audit --local`
- Phase 2 targeted validation:
  - `.venv/bin/python -m ruff check --select I --fix scripts/verify_live_regression.py tests/test_verify_live_regression_script.py`
  - `.venv/bin/python -m ruff format scripts/verify_live_regression.py tests/test_verify_live_regression_script.py`
  - `.venv/bin/python -m pytest tests/test_verify_live_regression_script.py -q --no-cov`
- Phase 5 supervisor rerun:
  - `.venv/bin/python -m ruff check src tests`
  - `.venv/bin/python -m ruff check scripts/verify_live_regression.py tests/test_verify_live_regression_script.py`
  - `.venv/bin/python -m mypy --strict src`
  - `.venv/bin/python -m pytest --cov=src --cov-branch --cov-report=term-missing`
  - per-file pylint important + changed script/test set (`--persistent=n`, `--fail-under=9.5`)
  - `.venv/bin/bandit -r src`
  - `.venv/bin/python -m deptry .`
  - `.venv/bin/pip-audit --local`
- result: `Done for phases 0-5. deptry residual handling is now formalized, requirements lock workflow drift is retired, live-regression evidence capture is materially stronger, and strict local gates pass.`
- blockers:
- `Phase 6` remains queued until commit/push/PR/check/merge lifecycle is executed.
- rollback note: `Revert this pass commit(s) to restore previous dependency policy/workflow behavior and live-regression tooling docs.`

- phase: `Phase 0 + Phase 1 + Phase 2 + Phase 3 + Phase 4 + Phase 5 (dependency-hardening-and-live-regression-prep)`
- objective: `Reduce dependency/security backlog, prepare large-PDF live regression validation, and rerun full supervisor gates`
- files changed:
- `requirements.txt`
- `requirements-dev.txt`
- `constraints.txt`
- `tests/test_summariser_refactored.py`
- `tests/test_summary_formatter.py`
- `scripts/verify_live_regression.py`
- `tests/test_verify_live_regression_script.py`
- `docs/LIVE_REGRESSION_LARGE_PDF.md`
- `docs/TESTING.md`
- `docs/CURRENT_STATE.md`
- `PLANS.md`
- commands run:
- phase 0 baseline:
  - `git status --short --branch`
  - `git log --oneline -5 --decorate`
  - `.venv/bin/python -m ruff check src tests`
  - `.venv/bin/python -m mypy --strict src`
  - `.venv/bin/python -m pytest --cov=src --cov-branch --cov-report=term-missing`
  - per-file pylint important set (`--fail-under=9.5`)
  - `.venv/bin/bandit -r src`
  - `.venv/bin/python -m deptry .`
  - `.venv/bin/pip-audit --local`
- phase 1 dependency hardening:
  - `.venv/bin/python -m pip install --dry-run protobuf==5.29.6 google-cloud-documentai==2.23.0`
  - `.venv/bin/python -m pip install --dry-run protobuf==5.29.6 google-cloud-documentai>=2.23.0`
  - `.venv/bin/python -m pip install -r requirements-dev.txt -c constraints.txt`
  - `.venv/bin/python -m pip check`
  - `.venv/bin/python -m deptry .`
  - `.venv/bin/pip-audit --local`
- phase 2 targeted validation:
  - `.venv/bin/python -m ruff check --select I --fix <changed python paths>`
  - `.venv/bin/python -m ruff format <changed python paths>`
  - `.venv/bin/python -m pytest tests/test_summariser_refactored.py tests/test_summary_formatter.py tests/test_verify_live_regression_script.py -q --no-cov`
- phase 5 supervisor pass:
  - `.venv/bin/python -m ruff check src tests`
  - `.venv/bin/python -m mypy --strict src`
  - `.venv/bin/python -m pytest --cov=src --cov-branch --cov-report=term-missing`
  - per-file pylint important + changed file set (`--fail-under=9.5`)
  - `.venv/bin/bandit -r src`
  - `.venv/bin/python -m deptry .`
  - `.venv/bin/pip-audit --local`
- result: `Done for phases 0-5. Dependency posture improved materially (pip-audit clean, deptry 82->2), large-PDF regression prep artifacts added, and full local supervisor matrix passed.`
- blockers:
- `deptry` retains `2` intentional DEP002 entries (`pillow`, `python-multipart`) pending explicit ignore-policy decision.
- rollback note: `Revert this pass commit(s) to restore previous dependency metadata and remove live-regression prep artifacts/tests.`

- phase: `Phase 0 + Phase 1 + Phase 2 + Phase 3 + Phase 4 (deps-and-summary-quality-pass continuation)`
- objective: `Recover/publish existing local branch work, resync repo venv, harden summarization content retention, and rerun full supervisor gates`
- files changed:
- `src/services/summariser_refactored.py`
- `tests/test_summariser_refactored.py`
- `docs/CURRENT_STATE.md`
- `PLANS.md`
- commands run:
- phase 0 branch checks:
  - `git status --short --branch`
  - `git log --oneline -5 --decorate`
  - `git diff --stat origin/main...HEAD`
  - `git rev-parse HEAD`
- phase 1 publish recovery:
  - `git remote -v`
  - `gh auth status`
  - `git ls-remote origin HEAD`
  - `git push -u origin codex/feat/deps-and-summary-quality-pass`
  - `gh pr list --head codex/feat/deps-and-summary-quality-pass ...`
  - `gh pr create --base main --head codex/feat/deps-and-summary-quality-pass ...`
  - `gh pr edit 28 --body ...`
- phase 2 dependency sync:
  - `test -x .venv/bin/python`
  - `.venv/bin/python --version`
  - `.venv/bin/python -m pip install -r requirements.txt`
  - `.venv/bin/python -m deptry .`
  - `.venv/bin/pip-audit --local`
  - `.venv/bin/python -m pip uninstall -y filelock`
  - `.venv/bin/python -m pip install --upgrade pip`
  - `.venv/bin/python -m pip install filelock==3.20.3`
  - `.venv/bin/pip-audit --local`
- phase 3 targeted validation:
  - `.venv/bin/python -m pytest tests/test_summariser_refactored.py -q --no-cov`
- phase 4 supervisor pass:
  - `.venv/bin/python -m ruff check --select I --fix src/services/summariser_refactored.py tests/test_summariser_refactored.py`
  - `.venv/bin/python -m ruff format src/services/summariser_refactored.py tests/test_summariser_refactored.py`
  - `.venv/bin/python -m ruff check src tests`
  - `.venv/bin/python -m mypy --strict src`
  - `.venv/bin/python -m pytest --cov=src --cov-branch --cov-report=term-missing`
  - `.venv/bin/python -m pylint --jobs=1 --score=y --fail-under=9.5 <important+changed summarization paths>`
  - `PYTHONPATH=. .venv/bin/python -m pylint --jobs=1 --score=y --fail-under=9.5 <per-file score capture>`
  - `.venv/bin/python -m bandit -r src`
  - `.venv/bin/python -m deptry .`
  - `.venv/bin/pip-audit --local`
- result: `Done for phases 0-4. Existing local branch work was preserved and published; PR #28 is open; summarization path now preserves non-generic overview/detail/plan content with passing targeted and full-suite tests.`
- blockers:
- `deptry` remains at `82` DEP002 findings (broad prune/split backlog).
- `pip-audit` residual advisories remain for `orjson`, `pillow`, and `protobuf` (higher-risk fix surface).
- rollback note: `Revert this continuation commit to restore previous strict filtering behavior in summariser and remove new regression expectations.`

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
  - `git push -u origin codex/feat/deps-and-summary-quality-pass` (attempted three times)
- result: `Done for phases 0-4 and local phase-5 commit creation. Large-PDF summarization path now uses SDK-valid structured output and improved formatting/aggregation behavior with passing tests and full repo gates.`
- blockers:
- `git fetch origin` failed twice with GitHub HTTP 500.
- `.venv` dependency sync blocked by DNS/PyPI resolution (`No matching distribution found` after repeated name-resolution failures), so vulnerability reduction could not be validated in the local environment.
- `git push -u origin codex/feat/deps-and-summary-quality-pass` failed three times with GitHub HTTP 500, blocking PR creation/merge for this pass.
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

- phase: `Phase 0 + Phase 1 + Phase 2 + Phase 3 + Phase 4 + Phase 5 + Phase 6 (config-align-live-runtime)`
- objective: `Use approved live runtime as operational truth and align repo-controlled deploy/docs with minimal safe changes, including pipeline.yaml status resolution and dependency metadata hygiene.`
- files changed:
- `cloudbuild.yaml`
- `README.md`
- `pipeline.yaml`
- `tests/test_infra_manifest.py`
- `requirements.txt`
- `docs/CURRENT_STATE.md`
- `PLANS.md`
- commands run:
- branch/bootstrap: `git fetch origin`, `git checkout main`, `git merge --ff-only origin/main`, `git checkout -b codex/feat/config-align-live-runtime`
- read-only verification:
  - `git status --short --branch`
  - `gcloud auth list --format='table(account,status)'`
  - `gcloud config list --format='text(core.project,core.account,compute.region,run.region,workflows.location)'`
  - `gcloud run services describe mcc-ocr-summary --region us-central1 --project quantify-agent --format='yaml(metadata.name,status.url,spec.template.spec.serviceAccountName,spec.template.metadata.annotations,spec.template.spec.containers,metadata.annotations)'`
  - `gcloud run services get-iam-policy mcc-ocr-summary --region us-central1 --project quantify-agent --format='json'`
  - supplemental projection: `gcloud run services describe ... --format='yaml(metadata.annotations,spec.template.metadata.annotations,spec.template.spec.containerConcurrency,spec.template.spec.timeoutSeconds,spec.template.spec.serviceAccountName,status.url)'`
- dependency tooling: `.venv/bin/python -m deptry .`, `.venv/bin/pip-audit --local`
- validation:
  - `.venv/bin/python -m ruff check src tests`
  - `.venv/bin/python -m mypy --strict src`
  - `.venv/bin/python -m pytest --cov=src --cov-branch --cov-report=term-missing`
  - `git diff --check`
- result: `Done for repo-local scope. Deploy/runtime config is aligned to verified live values; pipeline.yaml ambiguity is removed; reportlab declaration corrected; full validation matrix passed.`
- blockers: `No blocking issue for repo-local alignment. Vulnerability remediation is intentionally deferred to a dedicated dependency-hardening task.`
- rollback note: `Revert this pass commit to restore prior cloudbuild/runtime doc/manifest metadata state.`
## Validation
- Each phase must end with concrete command output and updated evidence.
- No phase is complete until tests relevant to the touched surface pass.
- `docs/CURRENT_STATE.md` must be refreshed whenever verified facts change.
## Failure Modes
- If target GCP environment is unclear, stop after Phase 0 and request a human decision.
- If a phase requires destructive cloud changes, convert that step into HUMAN MUST RUN and
continue only on repo-safe work.
- If two iterations fail to improve the same exit criterion, stop and escalate.
