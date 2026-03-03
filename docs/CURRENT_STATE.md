# docs/CURRENT_STATE.md — Verified Current State Register

Last updated: 2026-03-03 12:15:45 PST
Updated by: Codex (thread: deps-and-summary-quality-pass continuation)
Repo branch: `codex/feat/deps-and-summary-quality-pass`
Repo commit (branch baseline): `208ba3ad779165dd6e95318a98aaf9ab5613ad82`
Task id: `deps-and-summary-quality-pass`
Target GCP project: `quantify-agent` (canonical target)
Target region: `us-central1` (canonical target)
Cloud audit status: `NOT RUN THIS CONTINUATION (repo-local phases only; no cloud writes performed)`

## Phase Queue Status (this continuation)
- Phase 0: `DONE` (verified clean branch state, preserved local commit stack, confirmed unique branch diff vs `origin/main`)
- Phase 1: `DONE` (published branch to origin and opened PR `#28` after resolving sandboxed network/auth checks)
- Phase 2: `DONE` (repo-root `.venv` synced from `requirements.txt`; dependency tooling rerun successfully)
- Phase 3: `DONE` (additional summarization-quality fix applied with targeted regression tests)
- Phase 4: `DONE` (full supervisor matrix rerun; strict gates pass, with known residual dependency backlog)
- Phase 5: `QUEUED` (awaiting post-commit push/check/merge actions for this continuation)

## Phase 0 Verification Evidence
- `git status --short --branch` -> `## codex/feat/deps-and-summary-quality-pass` (clean)
- `git log --oneline -5 --decorate` -> HEAD `208ba3a`, branch contains unpublished task commits above `origin/main` `b1a020c`
- `git diff --stat origin/main...HEAD` -> `6` files changed (`288` insertions, `76` deletions) before continuation edits
- `git rev-parse HEAD` -> `208ba3ad779165dd6e95318a98aaf9ab5613ad82`

## Phase 1 Publish Recovery Evidence
- `git remote -v` -> `origin https://github.com/MJDinero/MCC-OCR-Summary.git`
- `gh auth status` (escalated) -> authenticated account `MJDinero`, valid token scopes include `repo` and `workflow`
- `git ls-remote origin HEAD` (escalated) -> connectivity restored
- `git push -u origin codex/feat/deps-and-summary-quality-pass` (escalated) -> `PASS` (new remote branch created)
- PR opened and corrected: `https://github.com/MJDinero/MCC-OCR-Summary/pull/28`

## Phase 2 Dependency Sync Results
- `test -x .venv/bin/python` -> `PASS`
- `.venv/bin/python --version` -> `Python 3.12.8`
- `.venv/bin/python -m pip install -r requirements.txt` (escalated) -> `PASS`
  - confirmed updates in repo venv: `python-multipart 0.0.22`, `urllib3 2.6.3`, `pypdf 6.7.4`, `pyasn1 0.6.2`, `wheel 0.46.2`
- `.venv/bin/python -m deptry .` -> `FAIL` (`82` DEP002 issues; down from `84`, direct `reportlab` DEP003 cleared in prior step)
- `.venv/bin/pip-audit --local` (escalated) -> improved to `6` vulnerabilities, later reduced to `3` after local env hygiene:
  - upgraded `pip` to `26.0.1`
  - installed patched `filelock==3.20.3` (tooling dependency for pip-audit)
  - remaining advisories: `orjson 3.11.3`, `pillow 12.0.0`, `protobuf 4.25.8`

## Phase 3 Summarization Quality Changes (this continuation)
- `src/services/summariser_refactored.py`
  - removed overly strict `"patient"` token gate when selecting overview lines
  - added fallback pass for `key_points`, `clinical_details`, and `care_plan` when keyword filtering would otherwise drop valid content
- `tests/test_summariser_refactored.py`
  - added regression that preserves overview content even without the word `"patient"`
  - added regression that preserves clinical/plan lines when keyword filters are too narrow

## Phase 4 Supervisor Validation Matrix (this continuation)
- `.venv/bin/python -m ruff check --select I --fix src/services/summariser_refactored.py tests/test_summariser_refactored.py` -> `PASS` (1 fix)
- `.venv/bin/python -m ruff format src/services/summariser_refactored.py tests/test_summariser_refactored.py` -> `PASS` (1 file reformatted)
- `.venv/bin/python -m ruff check src tests` -> `PASS`
- `.venv/bin/python -m mypy --strict src` -> `PASS` (`43` files)
- `.venv/bin/python -m pytest --cov=src --cov-branch --cov-report=term-missing` -> `PASS` (`195 passed`, `6 skipped`, coverage `96.66%`)
- `.venv/bin/python -m pylint --jobs=1 --score=y --fail-under=9.5 <important+changed summarization paths>` -> `PASS` (overall `9.90/10`)
- Per-file pylint scores:
  - `src/services/summariser_refactored.py` -> `9.87/10`
  - `src/services/summarization/formatter.py` -> `10.00/10`
  - `src/services/summarization/text_utils.py` -> `10.00/10`
  - `tests/test_summariser_refactored.py` -> `9.91/10`
- `.venv/bin/python -m bandit -r src` -> `LOW-ONLY FINDINGS` (`11 low`, `0 medium`, `0 high`)
- rerun dependency tools:
  - `.venv/bin/python -m deptry .` -> `82` issues (deferred backlog)
  - `.venv/bin/pip-audit --local` -> `3` vulnerabilities (deferred high-risk surface)

## Remaining Blockers / Deferred Risks
- `deptry` backlog (`82` DEP002 items) is still broad and requires a separate scoped dependency-pruning pass.
- `pip-audit` residual findings require higher-risk decisions:
  - `protobuf` requires major line jump (`5.29.6+` or `6.33.5`)
  - `orjson` advisory has no listed fix version
  - `pillow` fix (`12.1.1`) is transitive under `reportlab` and should be handled with a scoped compatibility check.

## Historical Snapshot (2026-03-02 phase6-reaudit-deps-closeout)

## Phase Queue Status (this pass)
- Phase 0: `DONE` (branch state verified; archive decision recorded)
- Phase 1: `DONE` (repo-root `.venv` verified)
- Phase 2: `DONE WITH BLOCKERS` (`deptry`/`pip-audit` blocked by DNS to PyPI)
- Phase 3: `DONE WITH BLOCKER` (gcloud account/project verified; ADC token refresh failed due DNS to `oauth2.googleapis.com`)
- Phase 4: `DONE` (full read-only GCP inventory command set executed)
- Phase 5: `DONE` (live-vs-repo drift classified)
- Phase 6: `DONE` (minimal repo-local ledger-only updates)
- Phase 7: `DONE` (docs-only integrity checks completed)
- Phase 8: `DONE` (this file + `PLANS.md` updated with evidence)

## Repo-Root `.venv` Verification
- `pwd` -> `/Users/quantanalytics/dev/MCC-OCR-Summary`
- `git rev-parse --show-toplevel` -> `/Users/quantanalytics/dev/MCC-OCR-Summary`
- `test -x .venv/bin/python` -> `0`
- `.venv/bin/python --version` -> `Python 3.12.8`

## Dependency Hygiene Results
- `command`: `.venv/bin/python -m pip install deptry pip-audit`
  - `result`: `BLOCKED`
  - `error`: repeated connection failures to `/simple/deptry/` and `No matching distribution found for deptry` with DNS/name-resolution failure.
- `command`: `.venv/bin/python -m deptry .`
  - `result`: `BLOCKED`
  - `error`: `No module named deptry`
- `command`: `.venv/bin/python -m pip_audit --local`
  - `result`: `BLOCKED`
  - `error`: `Failed to resolve 'pypi.org'`
- `fallback`: `.venv/bin/pip-audit --local`
  - `result`: `BLOCKED`
  - `error`: `Failed to resolve 'pypi.org'`

## Auth Verification
- `gcloud auth list --format='table(account,status)'` succeeded; active account is `Matt@moneymediausa.com`.
- `gcloud config list --format='text(core.project,core.account,compute.region,run.region,workflows.location)'` succeeded with:
  - `project=quantify-agent`
  - `account=Matt@moneymediausa.com`
  - `compute.region=us-central1`
  - `run.region=us-central1`
  - `workflows.location=us-central1`
- `gcloud auth application-default print-access-token >/dev/null` failed:
  - `error`: `Failed to resolve 'oauth2.googleapis.com'`

## Live GCP Values Observed (Read-Only)
- Cloud Run service: `mcc-ocr-summary`
- Cloud Run URLs:
  - status URL: `https://mcc-ocr-summary-6vupjpy5la-uc.a.run.app`
  - metadata URLs annotation includes both:
    - `https://mcc-ocr-summary-720850296638.us-central1.run.app`
    - `https://mcc-ocr-summary-6vupjpy5la-uc.a.run.app`
- Ingress posture: `run.googleapis.com/ingress=all` (and `ingress-status=all`)
- Service account: `mcc-orch-sa@quantify-agent.iam.gserviceaccount.com`
- Container image: `us-central1-docker.pkg.dev/quantify-agent/mcc/mcc-ocr-summary:ops-final-20251117-1613`
- Concurrency / timeout / max instances:
  - `containerConcurrency=1`
  - `timeoutSeconds=3600`
  - `autoscaling.knative.dev/maxScale=1`
- OCR processor IDs:
  - `DOC_AI_PROCESSOR_ID=21c8becfabc49de6`
  - `DOC_AI_OCR_PROCESSOR_ID=21c8becfabc49de6`
- Drive folder IDs:
  - `DRIVE_INPUT_FOLDER_ID=1eyMO0126VfLBK3bBQEpWlVOL6tWxriCE`
  - `DRIVE_REPORT_FOLDER_ID=130jJzsl3OBzMD8weGfBOaXikfEnD2KVg`
- Metrics setting: `ENABLE_METRICS=true`
- Buckets in runtime env:
  - `INTAKE_GCS_BUCKET=mcc-intake`
  - `OUTPUT_GCS_BUCKET=mcc-output`
  - `SUMMARY_BUCKET=mcc-output`
- Secrets wired in runtime env:
  - `OPENAI_API_KEY` -> Secret `OPENAI_API_KEY`
  - `INTERNAL_EVENT_TOKEN` -> Secret `internal-event-token`
  - `SERVICE_ACCOUNT_JSON` -> Secret `mcc_orch_sa_key`
- Inventory snapshots:
  - Artifact Registry repos: `cloud-run-source-deploy`, `mcc`, `mcc-artifacts`, `mcc-docker`, `mcc-ocr-summary`
  - Secret names visible (metadata only): `OPENAI_API_KEY`, `internal-event-token`, `mcc_orch_sa_key`, plus additional project secrets
  - Buckets include: `mcc-intake`, `mcc-output`, `mcc-state-quantify-agent-us-central1-322786`, `quantify-agent-mcc-phi-artifacts`, `quantify-agent-mcc-phi-raw`, and others
  - Workflow: `docai-pipeline` (ACTIVE)
  - Eventarc trigger: `mcc-intake-trigger` -> topic `eventarc-us-central1-mcc-intake-trigger-757`
  - Pub/Sub topics include: `mcc-intake`, `mcc-orchestrator`, `mcc-dlq`, `mcc-intake-dlq`, `mcc-ocr-pipeline-dlq`, eventarc topic
  - KMS keyring: `projects/quantify-agent/locations/us-central1/keyRings/mcc-keyring`
  - BigQuery datasets: `mcc_observability`, `run_googleapis_com`

## Drift Reconciliation
1. `repo stale, live likely correct`
   - `cloudbuild.yaml` `DRIVE_INPUT_FOLDER_ID` is `19xdu6hV9KNgnE_Slt4ogrJdASWXZb5gl` but live is `1eyMO0126VfLBK3bBQEpWlVOL6tWxriCE`.
   - `cloudbuild.yaml` `DRIVE_REPORT_FOLDER_ID` is `1eyMO0126VfLBK3bBQEpWlVOL6tWxriCE` but live is `130jJzsl3OBzMD8weGfBOaXikfEnD2KVg`.
   - `cloudbuild.yaml` sets `ENABLE_METRICS=false` but live is `true`.
   - `cloudbuild.yaml` `_TAG=v11mvp` but live revision runs `ops-final-20251117-1613`.
   - Live env has additional vars not present in `cloudbuild.yaml` (`SUMMARY_COMPOSE_MODE`, `PDF_WRITER_MODE`, `PDF_GUARD_ENABLED`, `ENABLE_NOISE_FILTERS`, `TAG_NAME`, `PDF_INPUT_FOLDER_ID`, `PDF_OUTPUT_FOLDER_ID`).
2. `live drift, repo likely intended`
   - No high-confidence item in this pass.
3. `documentation drift only`
   - `pipeline.yaml` still describes a two-container service (`mcc-orchestrator:latest` + GMP sidecar, `containerConcurrency=4`, `timeoutSeconds=1200`) and does not match the observed Cloud Run service spec.
   - `README.md` Drive mapping section contains folder IDs that do not match observed runtime env values.
4. `ambiguous, needs human decision`
   - Ingress posture is currently `all`; repo deploy truth (`cloudbuild.yaml`) does not explicitly enforce ingress or unauthenticated access policy.
   - Cloud Run service account/concurrency/timeout/max-scale are not explicitly pinned in `cloudbuild.yaml`; drift could recur on future deploys.

## Exact Blockers Remaining
- Dependency tooling is still blocked by DNS resolution to PyPI (`deptry` install and `pip-audit` index lookups).
- ADC token minting is blocked by DNS resolution to `oauth2.googleapis.com`; CLI read-only inventory still worked for the requested command set.

## Evidence Log (Commands Run This Pass)
- Phase 0: `git status --short --branch`, `git log --oneline -3 --decorate`, `git show --stat --name-status 683624b`, commit-content/reference inspection commands
- Read-first docs: `AGENTS.md`, `PLANS.md`, `docs/CURRENT_STATE.md`, `docs/REFACTOR_RUNBOOK.md`, `docs/ARCHITECTURE.md`, `docs/CODEBASE_MAP.md`, `docs/TESTING.md`, `docs/GCP_REFACTOR_PLAN.md`
- Phase 1: `pwd`, `git rev-parse --show-toplevel`, `test -x .venv/bin/python`, `ls .venv/bin/python`, `.venv/bin/python --version`
- Phase 2: `.venv/bin/python -m pip install deptry pip-audit`, `.venv/bin/python -m deptry .`, `.venv/bin/python -m pip_audit --local`, `.venv/bin/pip-audit --local`
- Phase 3: `gcloud auth list ...`, `gcloud config list ...`, `gcloud auth application-default print-access-token >/dev/null`
- Phase 4 command set:
  - `gcloud run services describe ...`
  - `gcloud artifacts repositories list ...`
  - `gcloud secrets list ...`
  - `gcloud storage buckets list ...`
  - `gcloud iam service-accounts list ...`
  - `gcloud projects get-iam-policy ...`
  - `gcloud workflows list ...`
  - `gcloud eventarc triggers list ...`
  - `gcloud pubsub topics list ...`
  - `gcloud kms keyrings list ...`
  - `bq ls --project_id=quantify-agent`
  - supplemental read-only projection for ingress/concurrency/timeout.
