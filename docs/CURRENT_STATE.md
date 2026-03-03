# docs/CURRENT_STATE.md — Verified Current State Register

Last updated: 2026-03-03 11:22:30 PST
Updated by: Codex (thread: deps-and-summary-quality-pass)
Repo branch: `codex/feat/deps-and-summary-quality-pass`
Repo commit (branch baseline): `b1a020c7f5d209916e17f5ed9f9e610e364d9b24`
Task id: `deps-and-summary-quality-pass`
Target GCP project: `quantify-agent` (canonical target)
Target region: `us-central1` (canonical target)
Cloud audit status: `NOT RUN THIS PASS (repo-local phases only; no cloud writes performed)`

## Phase Queue Status (this pass)
- Phase 0: `DONE WITH BLOCKER` (branch bootstrap + full local baseline matrix completed; `git fetch origin` failed twice with GitHub HTTP 500)
- Phase 1: `DONE WITH BLOCKER` (dependency pin patch prepared; `.venv` sync blocked by DNS/PyPI resolution after two focused retries)
- Phase 2: `DONE` (large-PDF summarization path investigated with code evidence; targeted fixes + characterization tests added)
- Phase 3: `DONE` (optional cleanup limited to inventory only; no broad delete/prune)
- Phase 4: `DONE` (full quality matrix rerun; strict gates green except known deptry/pip-audit backlog)
- Phase 5: `BLOCKED` (local commit created; push/PR blocked by repeated GitHub HTTP 500 errors)

## Branch Bootstrap (Required Sequence)
- `git fetch origin` -> `BLOCKED` (two attempts; `The requested URL returned error: 500`)
- `git checkout main` -> `Already on 'main'`
- `git merge --ff-only origin/main` -> `Already up to date`
- `git checkout -b codex/feat/deps-and-summary-quality-pass` -> `DONE`

## Baseline Validation Matrix (Phase 0)
- `.venv/bin/python -m ruff check src tests` -> `PASS`
- `.venv/bin/python -m mypy --strict src` -> `PASS` (`43` source files)
- `.venv/bin/python -m pytest --cov=src --cov-branch --cov-report=term-missing` -> `PASS` (`191 passed`, `6 skipped`, coverage `94.91%`)
- `.venv/bin/python -m pylint --jobs=1 --score=y --fail-under=9.5 <important files>` -> `PASS` (overall `9.75/10`; cache write warning only)
- `.venv/bin/bandit -r src` -> `LOW-ONLY FINDINGS` (`11 low`, `0 medium`, `0 high`)
- `.venv/bin/python -m deptry .` -> `FAIL` (`84` issues: high volume DEP002 + DEP003 for direct `reportlab` import)
- `.venv/bin/pip-audit --local` -> `FAIL` (`25` vulnerabilities across `10` packages)

## Rebuilt Queue for This Pass
### Dependency items safe to attempt now
- `python-multipart` `0.0.20` -> `>=0.0.22`
- `urllib3` `2.5.0` -> `>=2.6.3`
- `pypdf` runtime/env drift (`.venv` has `4.2.0`) -> align to patched `6.7.x`
- add direct `reportlab` requirement (`deptry` DEP003 on `src/services/pdf_writer.py`)

### Dependency items deferred/risky for this pass unless cleanly validated
- `protobuf` fix line requires major jump (`5.29.6+`) across Google client dependency surface
- `orjson` advisory has no fixed version listed in `pip-audit`; treat as remove-only if safely unused

### Summarization quality investigation hotspots (active path)
- Active runtime uses `RefactoredSummariser` (`src/main.py`) and `SummaryContract -> PDFWriterRefactored` (`src/api/process.py`).
- `OpenAIResponsesBackend.summarise_chunk` fallback behavior is narrow (only `TypeError`/`AttributeError`), which can push low-quality heuristic summaries in SDK mismatch scenarios.
- `clean_ocr_output` flattens whitespace/line breaks before summarization, reducing structural cues used by provider/signature extractors for long OCR text.
- `_compose_summary` currently pads short outputs by repeating filler fragments, which can produce repetitive low-coherence text blocks.

## Implemented in This Pass (Phases 1-4)
- `requirements.txt` updated for prioritized safe targets (`python-multipart`, `urllib3`, `pypdf`) plus direct `reportlab` declaration and additional security pin updates.
- `src/services/summariser_refactored.py` fixed OpenAI Responses structured-output call to use `text.format` JSON schema (active SDK-compatible path), relaxed over-strict detail/plan filtering, removed runtime chunk-count marker from summary text, switched provider-signature extraction to raw OCR source, and replaced low-coherence filler padding with structured supplemental context + deterministic fallback guidance.
- `src/services/summarization/formatter.py` removed technical chunk-count line from patient-facing provider section.
- `tests/test_summariser_refactored.py` expanded with characterization tests:
  - OpenAI backend uses `text.format` JSON schema and avoids deprecated `response_format`.
  - Runtime summary text omits chunk-count telemetry marker.

## Exact Blockers in This Pass
- `git fetch origin` currently blocked by remote GitHub HTTP 500; local baseline is still aligned to `origin/main` commit `b1a020c`.
- `.venv` dependency sync is blocked by DNS resolution failures to PyPI (`No matching distribution found` after repeated `nodename nor servname provided` errors), preventing local installation validation of upgraded requirement pins.
- `git push -u origin codex/feat/deps-and-summary-quality-pass` failed three times with GitHub HTTP 500, blocking PR creation and merge for this pass.

## Archive Commit Decision
- Decision: `KEEP` commit `683624b` (`docs: archive legacy audit reports`).
- Why: the commit only moved historical files from `audit/` to `docs/audit/archive/legacy/`, added `audit/README.md` as a forward pointer, and updated one README link. No active runtime/deploy/test files were moved and no active reference breaks were found.

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
