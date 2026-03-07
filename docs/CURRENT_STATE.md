# docs/CURRENT_STATE.md — Verified Current State Register

Last updated: 2026-03-07 03:40:50 PST
Updated by: Codex (thread: summary-p0-live-output-mismatch-livefix-smoke-guard)
Repo branch: `codex/summary-p0-live-output-mismatch-livefix-smoke-guard`
Repo commit (branch baseline): `9215f5e509f58461a700f1848e7f8460c98671e5`
Task id: `summary-p0-live-output-mismatch-livefix-smoke-guard`
Target GCP project: `quantify-agent` (canonical target)
Target region: `us-central1` (canonical target)
Cloud audit status: `BLOCKED (2026-03-07 read-only describe attempts failed because local gcloud auth requires interactive reauthentication)`
Pipeline status classification: `REPO_SMOKE_PROOF_HARDENED_AWAITING_HUMAN_GCLOUD_REAUTH`

## Phase Queue Status (current pass)
- Phase 0: `DONE` (read-first docs loaded; initial baseline captured on rescue branch `codex/summary-p0-live-output-mismatch-livefix-main-rescue-20260307` at `28b01d15da6783ceb1fbd5ca35fe7cc604afea1c`; authoritative live-fix commit `9215f5e509f58461a700f1848e7f8460c98671e5` identified and new working branch created from it)
- Phase 1: `DONE` (deploy contract reverified from authoritative branch content: `cloudbuild.yaml` deploys Cloud Run service, summariser job, PDF job, and workflow, and all three runtimes use `$_IMAGE_REPO:$_TAG`)
- Phase 2: `BLOCKED` (read-only gcloud checks for service/workflow/job drift and newest artifacts could not be rerun because `gcloud ... describe` failed with `Reauthentication failed. cannot prompt during non-interactive execution.`)
- Phase 3: `DONE` (repo-local smoke proof hardened so end-to-end verification now fails closed when summary JSON is legacy-shaped or still contains `Document processed in N chunk(s)` telemetry)
- Phase 4: `DONE` (required validation passed: `bash -n`, focused smoke tests, `ruff`, `mypy --strict src`, and repo-wide `pytest --cov=src --cov-report=term-missing`)
- Phase 5: `BLOCKED` (next meaningful step is human reauthentication followed by human-run deploy/live proof)

## Current Investigation Evidence (2026-03-07 smoke proof hardening)
- Baseline / branch proof:
  - initial `git status --short --branch` -> branch `codex/summary-p0-live-output-mismatch-livefix-main-rescue-20260307`, dirty only in local `.gcloud/*` state.
  - initial `git rev-parse HEAD` -> `28b01d15da6783ceb1fbd5ca35fe7cc604afea1c`.
  - `git fetch origin` completed successfully.
  - authoritative live-fix branch exists locally but is attached to another worktree: `codex/summary-p0-live-output-mismatch-livefix` -> `9215f5e`.
  - working branch for this pass: `codex/summary-p0-live-output-mismatch-livefix-smoke-guard` created from `9215f5e509f58461a700f1848e7f8460c98671e5`.
- Repo deploy-contract proof:
  - the initial rescue-branch working tree `cloudbuild.yaml` did not contain `gcloud run jobs deploy` steps.
  - `git show codex/summary-p0-live-output-mismatch-livefix:cloudbuild.yaml` confirmed the authoritative live-fix branch deploys:
    - Cloud Run service `mcc-ocr-summary`
    - Cloud Run job `mcc-ocr-summariser`
    - Cloud Run job `mcc-ocr-pdf-writer`
    - workflow `docai-pipeline`
  - the same authoritative manifest uses `--image=$_IMAGE_REPO:$_TAG` for the service and both jobs, which is the required deploy contract for eliminating async image drift.
- Read-only live-audit blocker proof:
  - attempted commands:
    - `gcloud run services describe mcc-ocr-summary --region us-central1 --project quantify-agent --format=json`
    - `gcloud run jobs describe mcc-ocr-summariser --region us-central1 --project quantify-agent --format=json`
    - `gcloud run jobs describe mcc-ocr-pdf-writer --region us-central1 --project quantify-agent --format=json`
    - `gcloud workflows describe docai-pipeline --location us-central1 --project quantify-agent --format=json`
  - all four failed with the same error: `Reauthentication failed. cannot prompt during non-interactive execution.`
  - local config still points at the intended target: `.gcloud/configurations/config_default` -> account `Matt@moneymediausa.com`, project `quantify-agent`.
- Repo-local smoke-proof gap proof:
  - before this patch, `scripts/e2e_smoke.sh` verified summary/PDF existence via `gcloud storage ls` but never inspected the summary JSON payload itself.
  - because of that gap, a stale summariser image could still satisfy the script so long as it produced any JSON object and PDF artifact.

## Patch Summary (current pass)
- `scripts/e2e_smoke.sh`
  - ported the testable/sourceable smoke harness shape onto the authoritative live-fix branch while preserving existing state-bucket/state-prefix arguments.
  - added `validate_refactored_summary_json` so the live proof now requires:
    - non-empty `schema_version`
    - non-empty `sections` array
    - per-section `slug`, `title`, `content`, and numeric `ordinal`
    - absence of top-level `Medical Summary`
    - absence of `Document processed in N chunk(s)` legacy marker text
  - dry-run output now explicitly includes the summary-contract validation step.
  - successful live output now includes `state_uri`, `summary_schema_version`, and `summary_sections`.
- `tests/test_e2e_smoke_script.py`
  - replaced text-inspection-only checks with executable source-mode tests for helper functions and contract validation behavior.
  - expanded coverage to dry-run/help output, URI helpers, Drive query construction, workflow execution matching, contract acceptance/rejection, cleanup behavior, and deterministic success output.

## Validation (current pass)
- `bash -n scripts/e2e_smoke.sh` -> `PASS`
- `.venv/bin/python -m pytest --no-cov tests/test_e2e_smoke_script.py` -> `PASS` (`21 passed`)
- `RUFF_CACHE_DIR=/tmp/mcc_ruff .venv/bin/python -m ruff check src tests` -> `PASS`
- `.venv/bin/python -m mypy --strict src` -> `PASS`
- `COVERAGE_FILE=/tmp/mcc_summary_smoke_guard.coverage .venv/bin/python -m pytest --cov=src --cov-report=term-missing` -> `PASS` (`249 passed`, `6 skipped`, total coverage `97.55%`)

## Remaining Risks / Unknowns (current pass)
- Live job/service/workflow drift was not revalidated in this pass because local gcloud auth is expired for non-interactive use.
- The repo now fails closed on legacy summary JSON during smoke proof, but a human still has to rerun live verification after reauthentication and any required deploy.
- Local `.gcloud/*` dirt remains intentionally unmodified and excluded from repo conclusions.

## Rollback (current pass)
- Revert `scripts/e2e_smoke.sh` and `tests/test_e2e_smoke_script.py` on `codex/summary-p0-live-output-mismatch-livefix-smoke-guard` to restore the previous smoke-proof behavior.

## Next Human Action (current pass)
- Reauthenticate local gcloud, then run the authoritative deploy and smoke-proof commands from this repo so live drift can be rechecked and, if needed, the summariser/PDF jobs can be updated.

## Historical Snapshot (2026-03-06 summary-p0-live-output-mismatch-livefix)

## Phase Queue Status (historical snapshot)
- Phase 0: `DONE` (read-first docs loaded; paired synthetic/real PDFs confirmed present; branch created from the validated async lane)
- Phase 1: `DONE` (paired PDF extraction plus live GCS/job inspection isolated the first failing stage to summary JSON generation before PDF rendering)
- Phase 2: `DONE` (repo deploy truth patched so Cloud Build now redeploys the summariser/PDF jobs that the async workflow actually runs, and explicitly reapplies workflow env vars from repo truth)
- Phase 3: `DONE` (regression tests added for job deploy truth, structured CLI output, and contract-first PDF rendering)
- Phase 4: `DONE` (structured async observability added for summary JSON, PDF, and Drive upload artifact tracing)
- Phase 5: `DONE` (targeted validation, route/config/smoke-adjacent checks, `ruff`, `mypy --strict`, and repo-wide coverage all passed)
- Phase 6: `DONE` (intended worktree ambiguity cleared: only task-relevant tracked changes remained, required gates were rerun, and the deploy candidate was normalized into a reviewable local branch commit)

## Current Investigation Evidence (2026-03-06 summary correctness P0)
- Baseline / branch proof:
  - `git status --short --branch` in the live-lane worktree initially showed a coherent tracked patchset on top of baseline `76fe30cfb221bde1f3adf1178876814fb18456e9`; no `.gcloud/*` or other local-only state was present in the intended worktree.
  - separate rescue worktree `/Users/quantanalytics/dev/MCC-OCR-Summary` retained the unrelated local `.gcloud/*` state on branch `codex/summary-p0-live-output-mismatch-livefix-main-rescue-20260307`.
  - required gates were rerun on `2026-03-06`, and the task-only patchset was normalized into a reviewable local deploy-candidate commit on `codex/summary-p0-live-output-mismatch-livefix`.
- Artifact proof:
  - paired PDFs exist locally for both synthetic and real runs.
  - extracted synthetic summary PDF text begins `Document Summary ... Intro Overview: Document processed in 1`.
  - extracted real summary PDF text begins `Document Summary ... Intro Overview: Document processed in 2`; the rendered PDFs are not cache reuse of each other because the chunk marker differs.
- Live summary JSON proof (first failing stage):
  - `gcloud storage cat gs://mcc-output/summaries/eeb4eff72da744c6a626ed2548442bf1.json`
    - synthetic output is already a legacy top-level payload with `Patient Information`, `Medical Summary`, and `Intro Overview: Document processed in 1 chunk(s)` before PDF rendering.
  - `gcloud storage cat gs://mcc-output/summaries/b283d66fdfea42e6994482e925f9df76.json`
    - real output is also already a legacy top-level payload before PDF rendering, with `Intro Overview: Document processed in 207 chunk(s)` plus raw OCR/header noise.
  - conclusion: repeated/cloned section bodies and runtime telemetry first appear in the stored summary JSON, not in the PDF job.
- Runtime path proof:
  - direct `/process` path still uses `src/api/process.py` -> `app.state.summariser.summarise_async(...)` -> `SummaryContract.from_mapping(...)` -> `PDFWriterRefactored.build(...)`.
  - live async workflow uses `workflows/pipeline.yaml` to run `mcc-ocr-summariser` and `mcc-ocr-pdf-writer` Cloud Run jobs over GCS artifacts rather than calling `/process`.
  - current `src/services/summariser_refactored.py` still contains the March 2026 fix that removed `Document processed in {chunk_count} chunk(s)` from composed patient-facing text.
- Live job drift proof:
  - `gcloud run jobs describe mcc-ocr-summariser --region us-central1 --project quantify-agent --format=json`
    - image: `us-central1-docker.pkg.dev/quantify-agent/mcc/mcc-ocr-summary:b853eb6`
    - last updated: `2025-10-14T06:53:44.925491Z`
    - command/args: `python -m src.services.summariser_refactored`
  - `gcloud run jobs describe mcc-ocr-pdf-writer --region us-central1 --project quantify-agent --format=json`
    - image: `us-central1-docker.pkg.dev/quantify-agent/mcc/mcc-ocr-summary:b853eb6`
    - last updated: `2025-10-14T06:53:57.071634Z`
    - command/args: `python -m src.services.pdf_writer_refactored`
  - the jobs are executing successfully on `2026-03-07`, but from stale October 2025 job definitions.
- Live service/workflow drift proof:
  - `gcloud run services describe mcc-ocr-summary --region us-central1 --project quantify-agent --format=json`
    - latest ready revision: `mcc-ocr-summary-00387-cmr`
    - image: `us-central1-docker.pkg.dev/quantify-agent/mcc/mcc-ocr-summary:ops-20260307-024251`
    - tag env: `TAG_NAME=ops-20260307-024251`
  - `gcloud workflows describe docai-pipeline --location us-central1 --project quantify-agent --format=json`
    - revision: `000023-49e`
    - update time: `2026-03-07T02:43:08.155840919Z`
  - `gcloud builds list --project quantify-agent --sort-by=~createTime --limit=5 --format=json`
    - latest successful build: `f0290956-763f-4d3b-8961-a7534fe3bb5d`
    - build image digest: `sha256:fa390f684fbac31dcf8f5a17a53f0de3c198f11fb7efa01b84a05bac33c06468`
    - build steps deployed the workflow and the Cloud Run service, but not the summariser/PDF jobs
- Repo deploy truth proof:
  - before this patch, `cloudbuild.yaml` deployed the service and workflow, but not the summariser/PDF jobs that the workflow actually invokes.
  - at the start of this pass, the workflow deploy step still relied on whatever `userEnvVars` were already present live; it did not reassert the workflow env contract from repo truth.
  - first true defect for this lane: deploy drift left the async summariser/PDF jobs on stale images, so the live async path bypassed current summary-quality fixes even though the service/workflow lane was up to date.
  - second deploy-truth defect in this pass: workflow repo deploys were not fully reproducible because required workflow env vars were not pinned in `cloudbuild.yaml`.

## Patch Summary (current pass)
- `cloudbuild.yaml`
  - added `gcloud run jobs deploy` steps for `$_SUMMARISER_JOB_NAME` and `$_PDF_JOB_NAME` using the current image `$_IMAGE_REPO:$_TAG`.
  - pinned both jobs to the verified runtime identity `$_JOB_SERVICE_ACCOUNT`.
  - pinned summariser entrypoint to `python -m src.services.summariser_refactored` and PDF entrypoint to `python -m src.services.pdf_writer_refactored`.
  - added the minimal pipeline-state/runtime envs the jobs need, plus the summariser `OPENAI_API_KEY` secret.
  - pinned workflow deploy env vars for `PIPELINE_SERVICE_BASE_URL`, project/region, DocAI processor/location, job names, bucket names, shard concurrency, and `WORKFLOW_CALLER_SA` so repo deploys reproduce the verified live workflow contract.
- Regression coverage
  - `tests/test_infra_manifest.py` now asserts Cloud Build deploys both jobs from the current image with the expected entrypoints, env vars, secret, and job service account.
  - `tests/test_infra_manifest.py` now also asserts the workflow deploy step reasserts the expected env vars from repo truth instead of relying on sticky live workflow config.
  - `tests/test_summariser_cli.py` now asserts CLI output is structured contract JSON with `schema_version`, `sections`, no legacy top-level summary blocks, and no `Document processed in` telemetry.
  - `tests/test_pdf_writer_refactored_unit.py` now asserts the PDF writer prefers canonical contract `sections` over legacy top-level `Medical Summary` text when both are present.
- Observability coverage
  - `src/services/summariser_refactored.py` now emits a structured `summary_done` record with `job_id`, `trace_id`, `stage=SUMMARY_JOB`, `summary_uri`, and the source `object_uri`.
  - `src/services/pdf_writer_refactored.py` now emits a structured `pdf_done` record with `job_id`, `trace_id`, `stage=PDF_JOB`, `summary_uri`, and `pdf_uri`.
  - `src/api/ingest.py` now emits a structured `workflow_drive_upload_complete` record with `job_id`, `trace_id`, `stage=DRIVE_UPLOAD`, `pdf_uri`, and `report_file_id`.
  - `src/utils/logging_utils.py` allowlists the async artifact-trace fields so JSON logs preserve them consistently.

## Validation (current pass)
- `bash -n scripts/e2e_smoke.sh` -> `PASS`
- `PYTHONPATH=$PWD /Users/quantanalytics/dev/MCC-OCR-Summary/.venv/bin/python -m pytest --no-cov tests/test_infra_manifest.py` -> `PASS` (`11 passed`)
- `/Users/quantanalytics/dev/MCC-OCR-Summary/.venv/bin/python -m pytest --no-cov tests/test_infra_manifest.py tests/test_summariser_cli.py tests/test_pdf_writer_refactored_unit.py` -> `PASS` (`22 passed`)
- `/Users/quantanalytics/dev/MCC-OCR-Summary/.venv/bin/python -m pytest --no-cov tests/test_e2e_smoke_script.py tests/test_infra_manifest.py tests/test_pipeline_endpoints.py tests/test_main_integration.py` -> `PASS` (`26 passed`, `1 warning`)
- `PYTHONPATH=$PWD /Users/quantanalytics/dev/MCC-OCR-Summary/.venv/bin/python -m pytest --no-cov tests/test_logging_setup.py tests/test_pipeline_endpoints.py tests/test_summariser_cli.py tests/test_signed_url.py` -> `PASS` (`18 passed`)
- `RUFF_CACHE_DIR=/tmp/mcc-drive-output-fix-ruff2 /Users/quantanalytics/dev/MCC-OCR-Summary/.venv/bin/python -m ruff check src tests` -> `PASS`
- `/Users/quantanalytics/dev/MCC-OCR-Summary/.venv/bin/python -m mypy --strict src` -> `PASS`
- `COVERAGE_FILE=/tmp/mcc-drive-output-fix.coverage /Users/quantanalytics/dev/MCC-OCR-Summary/.venv/bin/python -m pytest --cov=src --cov-report=term-missing` -> `PASS` (`231 passed`, `6 skipped`, total coverage `97.55%`)

## Remaining Risks / Unknowns (current pass)
- The repo-local defect is patched, but the stale live jobs will remain stale until a human actually deploys the updated `cloudbuild.yaml`.
- The real live summary JSON audit contained PHI; only sanitized structural facts are recorded here.
- The new structured artifact logs are validated locally; they will not appear in live Cloud Logging until the patched branch is deployed.
- No further repo-local ambiguity remains in the intended lane; the next meaningful boundary is explicit approval for cloud writes.

## Rollback (current pass)
- Revert the `cloudbuild.yaml` and test changes on this branch to restore the prior deploy behavior that left jobs untouched.

## Next Human Action (current pass)
- Deploy from the patched branch so Cloud Build updates the summariser/PDF jobs, then rerun the synthetic and real live proofs to confirm the stored summary JSON and final PDF now use the structured contract and emit the new async artifact-trace logs.

## Phase Queue Status (current pass)
- Phase 0: `DONE` (baseline synced on `main` at `76fe30cfb221bde1f3adf1178876814fb18456e9`; pre-existing dirty files isolated by using a new worktree)
- Phase 1: `DONE` (read-first docs + evidence-first cloud diagnosis)
- Phase 2: `DONE` (minimal code/workflow patch for workflow-driven Drive output upload)
- Phase 3: `DONE` (closeout audit repaired adapter/runtime, workflow state contract, deploy/proof tooling drift, and proof-path regression coverage; required validation gates pass)
- Phase 4: `DONE` (service/workflow redeployed, live synthetic proof succeeded, and the macOS smoke-script portability defect was repaired)

## Current Investigation Evidence (2026-03-06)
- Baseline proof:
  - `git fetch origin`
  - `git checkout main`
  - `git merge --ff-only origin/main`
  - result: `main` at `76fe30cfb221bde1f3adf1178876814fb18456e9`, but not clean because `.gcloud/*`, `scripts/e2e_smoke.sh`, and `tests/test_e2e_smoke_script.py` already had local changes.
- Auth/config proof:
  - `gcloud auth list` -> active account `Matt@moneymediausa.com`
  - `gcloud config get-value account` -> `Matt@moneymediausa.com`
  - `gcloud config get-value project` -> `quantify-agent`
- Live workflow proof:
  - REST snapshot: `/tmp/drive-output-audit/workflow.describe.json`
  - `revisionId` -> `000020-078`
  - `sourceContents` contains only internal event callbacks to `/ingest/internal/jobs/{jobId}/events`
  - `sourceContents` does not contain `upload-report`, `report_file_id`, `drive`, or `--skip-signed-url`
- Live service proof:
  - REST snapshot: `/tmp/drive-output-audit/run.service.json`
  - latest ready revision: `mcc-ocr-summary-00383-9wm`
  - service account: `mcc-orch-sa@quantify-agent.iam.gserviceaccount.com`
  - env includes `DRIVE_ENABLED=true`, `WRITE_TO_DRIVE=true`, `DRIVE_REPORT_FOLDER_ID=130jJzsl3OBzMD8weGfBOaXikfEnD2KVg`, and `PIPELINE_SERVICE_BASE_URL=https://mcc-ocr-summary-6vupjpy5la-uc.a.run.app`
- Live job proof:
  - REST snapshots: `/tmp/drive-output-audit/run.job.pdf.json`, `/tmp/drive-output-audit/run.job.summariser.json`
  - both jobs use `mcc-orchestrator-runtime@quantify-agent.iam.gserviceaccount.com`
  - neither job exposes Drive env vars or `WRITE_TO_DRIVE`
- Recent execution proof:
  - REST snapshot: `/tmp/drive-output-audit/executions.list.json`
  - last 10 executions include six recent `SUCCEEDED` runs on `2026-03-06` between `16:25:00Z` and `19:17:11Z`
- Live redeploy + proof update:
  - `gcloud builds submit --config cloudbuild.yaml --project quantify-agent --substitutions=_PROJECT_ID=quantify-agent,_REGION=us-central1,_TAG=ops-drive-output-fix-20260307-012458`
    - build `e253210e-c7b4-4f3d-aada-8c50eee13b1e` built/pushed the image and deployed the service, but failed the workflow step with `PERMISSION_DENIED: workflows.workflows.get` because the Cloud Build worker identity `720850296638-compute@developer.gserviceaccount.com` lacks workflow deploy permission.
  - `gcloud run services describe mcc-ocr-summary --region us-central1 --project quantify-agent --format='value(status.latestReadyRevisionName,status.url)'`
    - current live service: revision `mcc-ocr-summary-00385-lk7`
    - stable URL: `https://mcc-ocr-summary-6vupjpy5la-uc.a.run.app`
  - `gcloud workflows deploy docai-pipeline --source=workflows/pipeline.yaml --project quantify-agent --location us-central1 --service-account 720850296638-compute@developer.gserviceaccount.com`
    - revision `000021-b51`
    - update time `2026-03-07T01:28:50.439252289Z`
  - Live synthetic proof:
    - input Drive file: `1uzrrELJBH-YWVu0CvlY6MNp7yej29Tb3` (`synthetic-non-phi-2026-03-07T01-32-09Z.pdf`)
    - workflow execution: `projects/720850296638/locations/us-central1/workflows/docai-pipeline/executions/70cb9b6d-6d04-4bf3-a7b9-f6890ac10248`
    - state: `SUCCEEDED`
    - start: `2026-03-07T01:32:15.323454502Z`
    - end: `2026-03-07T01:38:49.400460871Z`
    - `job_id`: `f2963ef9b3da4eccbaae2cc006a82d12`
    - artifacts exist:
      - `gs://mcc-output/summaries/f2963ef9b3da4eccbaae2cc006a82d12.json`
      - `gs://mcc-output/pdf/f2963ef9b3da4eccbaae2cc006a82d12.pdf`
      - `gs://mcc-state-quantify-agent-us-central1-322786/pipeline-state/jobs/f2963ef9b3da4eccbaae2cc006a82d12.json`
    - Drive OUTPUT proof:
      - `report_file_id=1ELge8L4ucXXDANGZcL21cKURQfwPFeva`
      - persisted state matched `pdf_uri=gs://mcc-output/pdf/f2963ef9b3da4eccbaae2cc006a82d12.pdf`
      - persisted state matched `metadata.report_file_id=1ELge8L4ucXXDANGZcL21cKURQfwPFeva`
- Repo code-path proof:
  - synchronous Drive upload exists in `src/api/process.py`
  - pre-patch workflow path in `workflows/pipeline.yaml` generated the PDF in GCS and marked `UPLOADED`, but never called a Drive export endpoint

## Patch Summary (current pass)
- Added authenticated internal endpoint:
  - `POST /ingest/internal/jobs/{job_id}/upload-report`
  - downloads `pdfUri` from GCS and uploads to Drive via existing `drive_client.upload_pdf`.
- Updated workflow:
  - PDF job args now include `--skip-signed-url` because the workflow no longer consumes signed URLs.
  - workflow now calls new `/upload-report` endpoint and records `report_file_id` in upload status event.
- Repaired repo-local closeout drift:
  - `src/main.py` now accepts workflow-supplied `report_name` in the real Drive client adapter, preventing runtime `TypeError` on the new endpoint path.
  - `workflows/pipeline.yaml` now persists `pdf_uri` at `PDF_DONE` and `report_file_id` in job metadata at `UPLOADED`, so `/ingest/status/{job_id}` retains the final GCS/Drive artifact contract.
  - `cloudbuild.yaml` now deploys `workflows/pipeline.yaml` via `_WORKFLOW_NAME` and aligns the service env `PIPELINE_WORKFLOW_NAME` with the same substitution.
  - `scripts/e2e_smoke.sh` now verifies `summary-<job_id>.pdf` in the Drive OUTPUT folder, validates the persisted state object `gs://<state-bucket>/pipeline-state/jobs/<job_id>.json` for matching `pdf_uri` and `metadata.report_file_id`, emits `state_uri`, and no longer depends on Bash 4-only `mapfile`; `README.md` human-run commands now match repo truth.
- Added regression guards/tests:
  - infra tests assert workflow has upload callback and `--skip-signed-url`.
  - endpoint tests validate the successful Drive upload call path through the real adapter and `WRITE_TO_DRIVE=false` fail-closed behavior.
  - smoke-script tests assert the dry-run plan now includes Drive OUTPUT verification and reject Bash 4-only `mapfile`.
  - workflow launcher tests assert Cloud Workflows execution arguments preserve `metadata.drive_file_id`, which `scripts/e2e_smoke.sh` uses to match the correct live execution.

## Validation (current pass)
- `bash -n scripts/e2e_smoke.sh` -> `PASS`
- `PYTHONPATH=$PWD /Users/quantanalytics/dev/MCC-OCR-Summary/.venv/bin/python -m pytest --no-cov tests/test_e2e_smoke_script.py -q -rA` -> `PASS` (`3 passed`)
- `PYTHONPATH=$PWD /Users/quantanalytics/dev/MCC-OCR-Summary/.venv/bin/python -m pytest --no-cov tests/test_pipeline_endpoints.py tests/test_infra_manifest.py tests/test_e2e_smoke_script.py tests/test_pipeline_workflow_launcher.py -q -rA` -> `PASS` (`24 passed`)
- `PYTHONPATH=$PWD /Users/quantanalytics/dev/MCC-OCR-Summary/.venv/bin/python -m pytest --no-cov tests/test_health_metrics.py::test_healthz_ok tests/test_pipeline_endpoints.py::test_status_endpoint_returns_job -q` -> `PASS` (`2 passed`)
- `PYTHONPATH=$PWD RUFF_CACHE_DIR=/tmp/mcc-drive-output-proof-ruff /Users/quantanalytics/dev/MCC-OCR-Summary/.venv/bin/python -m ruff check src tests` -> `PASS`
- `PYTHONPATH=$PWD /Users/quantanalytics/dev/MCC-OCR-Summary/.venv/bin/python -m mypy --strict src` -> `PASS`
- `PYTHONPATH=$PWD COVERAGE_FILE=/tmp/mcc-drive-output-closeout-4.coverage /Users/quantanalytics/dev/MCC-OCR-Summary/.venv/bin/python -m pytest --cov=src --cov-report=term-missing` -> `PASS` (`227 passed`, `6 skipped`, `97.55%` coverage)

## Remaining Risks / Unknowns (current pass)
- The repo-local runtime path is now proven live, but the one-command Cloud Build service+workflow deploy path is still blocked by external workflow IAM on the Cloud Build worker identity.
- The long-running shell session for the second `scripts/e2e_smoke.sh` invocation did not emit its trailing summary lines in this app session, although the matched execution, artifacts, Drive OUTPUT file, and persisted state all verified successfully via direct follow-up commands.

## Rollback (current pass)
- Revert this branch commit to restore the previous ingest/workflow/Cloud Build/smoke-script behavior.

## Next Human Action (current pass)
- Decide whether to grant the Cloud Build worker identity `720850296638-compute@developer.gserviceaccount.com` the workflow deploy permissions it currently lacks, or to keep workflow deploy as a separate user-authenticated command outside Cloud Build.

## Historical Snapshot (2026-03-05 workflow-callback-path-repair)

## Historical Snapshot (2026-03-05 pipeline-runtime-env-contract-repair)

## Historical Snapshot (2026-03-05 workflow-init-contract-repair)

## Historical Snapshot (2026-03-04 cmek-default-alignment)

## Historical Snapshot (2026-03-03 final-hardening-and-regression-orchestration)

## Phase Queue Status (current pass)
- Phase 0: `DONE` (branch/log baseline and full local validation matrix collected; queue rebuilt)
- Phase 1: `DONE` (formalized intentional `deptry` policy and reconciled dependency workflow drift)
- Phase 2: `DONE` (upgraded live-regression runbook + evidence/scoring tooling)
- Phase 3: `DEFERRED` (no new summarization defect surfaced by new evidence)
- Phase 4: `DONE` (targeted cleanup complete; stale lock workflow retired without broad deletion)
- Phase 5: `DONE` (strict supervisor rerun and consistency review complete)
- Phase 6: `QUEUED` (commit/push/PR/merge lifecycle)

## Phase 0 Baseline + Queue Rebuild (current pass)
- `git status --short --branch` -> `## codex/feat/final-hardening-and-regression-orchestration`
- `git log --oneline -5 --decorate` -> HEAD `1ba9715` (merged PR `#29`)
- Baseline validation before patching:
  - `.venv/bin/python -m ruff check src tests` -> `PASS`
  - `.venv/bin/python -m mypy --strict src` -> `PASS`
  - `.venv/bin/python -m pytest --cov=src --cov-branch --cov-report=term-missing` -> `PASS` (`203 passed`, `6 skipped`, coverage `97.14%`)
  - per-file `pylint` important set (`--persistent=n`, `--fail-under=9.5`) -> `PASS`
    - `src.main` `10.00/10`
    - `src.services.storage_service` `9.81/10`
    - `src.services.pipeline` `9.86/10`
    - `src.api.ingest` `10.00/10`
    - `src.api.process` `10.00/10`
    - `src.services.summariser_refactored` `9.87/10`
    - `src.services.summarization.formatter` `10.00/10`
    - `src.services.summarization.text_utils` `10.00/10`
  - `.venv/bin/bandit -r src` -> `11 low`, `0 medium`, `0 high`
  - `.venv/bin/python -m deptry .` -> `2` DEP002 issues (`pillow`, `python-multipart`)
  - `.venv/bin/pip-audit --local` -> `No known vulnerabilities found`

## Rebuilt Queue Classification (current pass)
- dependency-policy cleanup:
  - `DONE`: intentional `deptry` DEP002 residual handling for `pillow` and `python-multipart` encoded in `pyproject.toml` + documented in `docs/DEPENDENCY_POLICY.md`.
- dependency-process drift:
  - `DONE`: stale `requirements.lock` retired; local/CI dependency workflows aligned on `requirements.txt` + `requirements-dev.txt` + `constraints.txt`.
- live-regression support:
  - `DONE`: runbook/helper artifacts now include expected-ID comparison, strict key checks, duplicate detection, and scorecard output.
- human boundary:
  - real large-PDF live execution remains human-invoked only; repo work stayed read-only/tooling/docs.

## Phase 1 Dependency Policy + Workflow Reconciliation (current pass)
- Policy encoding changes:
  - added `pyproject.toml` with `[tool.deptry]` configuration.
  - added explicit `DEP002` per-rule ignores for intentional runtime dependencies:
    - `pillow`
    - `python-multipart`
  - annotated intent inline in `requirements.txt`.
  - added `docs/DEPENDENCY_POLICY.md` as dependency process source-of-truth.
- Workflow drift reconciliation:
  - retired stale `requirements.lock`.
  - aligned CI dependency installation with constraints:
    - `.github/workflows/ci.yml` now installs `requirements-dev.txt` with `-c constraints.txt`.
  - updated `README.md` and `docs/TESTING.md` dependency workflow guidance.
- Validation:
  - `.venv/bin/python -m deptry .` -> `PASS` (`Success! No dependency issues found.`)
  - `.venv/bin/pip-audit --local` -> `PASS` (`No known vulnerabilities found`)

## Phase 2 Live-Regression Tooling/Runbook Upgrade (current pass)
- `scripts/verify_live_regression.py` improvements:
  - optional expected source ID set validation via `--expected-file-ids-file`
  - strict response key-shape validation via `--strict-keys`
  - duplicate detection for `report_file_id` and `request_id`
  - optional success-rate threshold via `--min-success-rate`
  - scorecard artifact output via `--scorecard-out`
- Runbook/docs artifacts:
  - updated `docs/LIVE_REGRESSION_LARGE_PDF.md` with deterministic artifact layout, scoring rubric, and stepwise evidence flow.
  - added `docs/LIVE_REGRESSION_EVIDENCE_TEMPLATE.md`.
  - added `docs/LIVE_REGRESSION_EXPECTED_IDS.example.txt`.
- Test coverage:
  - expanded `tests/test_verify_live_regression_script.py` with scorecard, expected-ID mismatch, and duplicate-ID regression cases.

## Phase 3 Targeted Summarization Refinement Decision (current pass)
- Status: `DEFERRED`
- Evidence: phase-2 helper/test additions did not reveal a new summarization correctness defect requiring source changes.

## Phase 4 Optional Cleanup Inventory (current pass)
- Completed targeted safe cleanup:
  - removed stale `requirements.lock` to eliminate dependency-process ambiguity.
- No broad deletion or architecture cleanup performed.

## Phase 5 Supervisor Validation Matrix (current pass)
- `.venv/bin/python -m ruff check src tests` -> `PASS`
- `.venv/bin/python -m ruff check scripts/verify_live_regression.py tests/test_verify_live_regression_script.py` -> `PASS`
- `.venv/bin/python -m mypy --strict src` -> `PASS`
- `.venv/bin/python -m pytest --cov=src --cov-branch --cov-report=term-missing` -> `PASS`
  - `205 passed`, `6 skipped`, coverage `97.14%`
- Per-file pylint (`--persistent=n`, `--jobs=1`, `--fail-under=9.5`) -> `PASS`
  - `src.main` -> `10.00/10`
  - `src.services.storage_service` -> `9.81/10`
  - `src.services.pipeline` -> `9.86/10`
  - `src.api.ingest` -> `10.00/10`
  - `src.api.process` -> `10.00/10`
  - `src.services.summariser_refactored` -> `9.87/10`
  - `src.services.summarization.formatter` -> `10.00/10`
  - `src.services.summarization.text_utils` -> `10.00/10`
  - `scripts/verify_live_regression.py` -> `9.95/10`
  - `tests/test_verify_live_regression_script.py` -> `10.00/10`
- `.venv/bin/bandit -r src` -> `LOW-ONLY FINDINGS` (`11 low`, `0 medium`, `0 high`)
- `.venv/bin/python -m deptry .` -> `PASS` (`Success! No dependency issues found.`)
- `.venv/bin/pip-audit --local` -> `PASS` (`No known vulnerabilities found`)

## Remaining Risks / Deferred Items (current pass)
- Real large-PDF live regression execution remains human-invoked and not executed in this repo-local pass.
- Bandit still reports low-severity findings in unchanged legacy surfaces.
- No cloud writes performed in this pass.

## Historical Snapshot (2026-03-03 dependency-hardening-and-live-regression-prep)

## Phase 1 Dependency Hardening Results
- Compatibility evidence:
  - `.venv/bin/python -m pip install --dry-run protobuf==5.29.6 google-cloud-documentai==2.23.0` -> `ResolutionImpossible` (`documentai<5.0.0dev` blocker)
  - `.venv/bin/python -m pip install --dry-run protobuf==5.29.6 google-cloud-documentai>=2.23.0` -> resolver selects `google-cloud-documentai==3.10.0` and succeeds
- Changes applied:
  - `requirements.txt` reduced to direct runtime dependencies
  - `requirements-dev.txt` now owns test/tooling dependencies (`bandit`, `deptry`, `pip-audit`, etc.)
  - `constraints.txt` updated to `protobuf>=5.29.6,<6.0.0`
  - `orjson` removed from runtime dependency surface and uninstalled from `.venv`
  - upgraded: `google-cloud-documentai 2.23.0 -> 3.10.0`, `protobuf 4.25.8 -> 5.29.6`, `pillow 12.0.0 -> 12.1.1`
- Post-change dependency validation:
  - `.venv/bin/python -m pip check` -> `No broken requirements found`
  - `.venv/bin/pip-audit --local` -> `No known vulnerabilities found`
  - `.venv/bin/python -m deptry .` -> `2` DEP002 findings (`pillow`, `python-multipart`, both intentional runtime non-import dependencies)

## Phase 2 Large-PDF Regression Preparation
- Verified runtime path for long-document summarization:
  - `src/api/process.py::_execute_pipeline` -> `app.state.summariser.summarise_async`
  - `src/main.py::_build_summariser` wires `RefactoredSummariser`
  - `src/services/summariser_refactored.py::summarise` -> `_compose_summary`
  - `src/services/summarization/formatter.py::build_mcc_bible_sections`
- Added characterization/contract coverage:
  - `tests/test_summariser_refactored.py`
    - `test_large_ocr_like_input_retains_content_and_claim_evidence`
  - `tests/test_summary_formatter.py`
    - heading and blank-line/fallback formatting guards
- Added live-regression prep artifacts (human-invoked only):
  - `docs/LIVE_REGRESSION_LARGE_PDF.md`
  - `scripts/verify_live_regression.py`
  - `tests/test_verify_live_regression_script.py`
  - `docs/TESTING.md` updated with live large-PDF regression section

## Phase 3 Targeted Summarization Refinement Decision
- Status: `DEFERRED (no additional code patch required in this pass)`
- Evidence: new large-input retention/contract tests pass without exposing a new correctness defect requiring source changes.

## Phase 4 Optional Cleanup Inventory (No Broad Purge)
- Candidate inventory only:
  - follow-up option to codify `deptry` ignores for intentional runtime plugin/transitive dependencies
  - follow-up option to reconcile or retire stale `requirements.lock` generation workflow

## Phase 5 Supervisor Validation Matrix (Final)
- `.venv/bin/python -m ruff check src tests` -> `PASS`
- `.venv/bin/python -m mypy --strict src` -> `PASS`
- `.venv/bin/python -m pytest --cov=src --cov-branch --cov-report=term-missing` -> `PASS`
  - `203 passed`, `6 skipped`, coverage `97.14%`
- Per-file pylint (important + changed Python files): all `>= 9.5`
  - `src.main` -> `10.00/10`
  - `src.services.storage_service` -> `9.81/10`
  - `src.services.pipeline` -> `9.86/10`
  - `src.api.ingest` -> `10.00/10`
  - `src.api.process` -> `10.00/10`
  - `src.services.summariser_refactored` -> `9.87/10`
  - `src.services.summarization.formatter` -> `10.00/10`
  - `tests.test_summariser_refactored` -> `9.92/10`
  - `tests.test_summary_formatter` -> `10.00/10`
  - `tests.test_verify_live_regression_script` -> `10.00/10`
  - `scripts/verify_live_regression.py` -> `10.00/10`
- `.venv/bin/bandit -r src` -> `LOW-ONLY FINDINGS` (`11 low`, `0 medium`, `0 high`)
- `.venv/bin/python -m deptry .` -> `2` issues (`pillow`, `python-multipart`)
- `.venv/bin/pip-audit --local` -> `No known vulnerabilities found`

## Remaining Risks / Deferred Items
- `deptry` residual `DEP002` entries are intentional:
  - `pillow` is pinned for transitive `reportlab` security hygiene.
  - `python-multipart` is runtime-required by FastAPI multipart/form parsing despite no direct import.
- No cloud writes performed in this pass.

## Historical Snapshot (2026-03-03 config-align-live-runtime)
- Last updated: `2026-03-03 10:26:53 PST`
- Updated by: `Codex (thread: config-align-live-runtime)`
- Repo branch: `codex/feat/config-align-live-runtime`
- Repo commit (branch baseline): `b1a020c3c99f4fb4879c11735ddc029ba8305bcc`
- Task id: `config-align-live-runtime`
- Target GCP project: `quantify-agent` (approved canonical target)
- Target region: `us-central1` (approved canonical target)
- Cloud audit status: `DONE (read-only commands only; no cloud writes)`

## Phase Queue Status (this pass)
- Phase 0: `DONE` (repo + live runtime re-verified with read-only commands)
- Phase 1: `DONE` (live-vs-repo alignment matrix built and classified)
- Phase 2: `DONE` (repo-controlled deploy/docs aligned to approved live runtime)
- Phase 3: `DONE` (`pipeline.yaml` explicitly marked legacy/non-authoritative)
- Phase 4: `DONE` (`reportlab` direct dependency declaration fixed; vulnerabilities inventoried/prioritized)
- Phase 5: `DONE` (full required validation commands passed)
- Phase 6: `DONE` (strict self-review complete; scope stayed config/docs/metadata-focused)
- Phase 7: `BLOCKED` (awaiting post-update commit/push/PR lifecycle)

## Live Runtime Values Re-Verified (2026-03-03)
- Project/region: `quantify-agent` / `us-central1`
- Cloud Run service: `mcc-ocr-summary`
- Cloud Run URLs:
  - `https://mcc-ocr-summary-6vupjpy5la-uc.a.run.app`
  - `https://mcc-ocr-summary-720850296638.us-central1.run.app`
- Service account: `mcc-orch-sa@quantify-agent.iam.gserviceaccount.com`
- Ingress: `all`
- Auth posture from IAM policy: `roles/run.invoker` bound only to service accounts; no `allUsers`/`allAuthenticatedUsers` binding observed.
- Image/tag: `us-central1-docker.pkg.dev/quantify-agent/mcc/mcc-ocr-summary:ops-final-20251117-1613`
- Runtime controls:
  - `containerConcurrency=1`
  - `timeoutSeconds=3600`
  - `autoscaling.knative.dev/maxScale=1`
- OCR processor IDs:
  - `DOC_AI_PROCESSOR_ID=21c8becfabc49de6`
  - `DOC_AI_OCR_PROCESSOR_ID=21c8becfabc49de6`
- Drive IDs:
  - `DRIVE_INPUT_FOLDER_ID=1eyMO0126VfLBK3bBQEpWlVOL6tWxriCE`
  - `DRIVE_REPORT_FOLDER_ID=130jJzsl3OBzMD8weGfBOaXikfEnD2KVg`
- Metrics flag: `ENABLE_METRICS=true`
- Runtime buckets:
  - `INTAKE_GCS_BUCKET=mcc-intake`
  - `OUTPUT_GCS_BUCKET=mcc-output`
  - `SUMMARY_BUCKET=mcc-output`

## Alignment Matrix (Live Truth vs Repo)

| Surface | Live Runtime (2026-03-03) | Repo Before This Pass | Classification | Outcome |
| --- | --- | --- | --- | --- |
| Project / region | `quantify-agent` / `us-central1` | same in `cloudbuild.yaml` | `safe to align automatically` | no change needed |
| Cloud Run URLs | both URLs above | mostly tracked in docs, not deploy-pinned | `documentation clarification only` | refreshed in this file |
| Service account | `mcc-orch-sa@...` | not explicitly pinned in `cloudbuild.yaml` | `safe to align automatically` | pinned with `_SERVICE_ACCOUNT` + deploy arg |
| Ingress + IAM posture | ingress `all`; invoker restricted to service accounts | ingress/auth not explicit in deploy config | `security-sensitive` | ingress pinned in `cloudbuild.yaml`; IAM remains HUMAN MUST RUN |
| Image/tag | `ops-final-20251117-1613` | `_TAG=v11mvp` | `safe to align automatically` | `_TAG` default aligned to verified live tag |
| OCR processor ID | `21c8becfabc49de6` | already matched | `safe to align automatically` | no functional change |
| Drive input/report IDs | `1eyMO...` / `130jJ...` | stale values in `cloudbuild.yaml` + `README.md` | `safe to align automatically` | updated config/docs to live IDs |
| Metrics flag | `true` | `ENABLE_METRICS=false` in `cloudbuild.yaml` | `safe to align automatically` | set `ENABLE_METRICS=true` |
| Buckets | `mcc-intake`, `mcc-output`, `mcc-output` | already matched | `safe to align automatically` | no functional change |
| Concurrency / timeout / maxScale | `1` / `3600` / `1` | not pinned in `cloudbuild.yaml` | `safe to align automatically` | deploy flags pinned (`--concurrency`, `--timeout`, `--max-instances`) |
| `pipeline.yaml` status | live runtime does not use its spec | ambiguous legacy manifest with stale runtime values | `documentation clarification only` | marked as `legacy-reference-only`; tests enforce explicit status |

## `pipeline.yaml` Decision
- Decision: `B` (not authoritative deployment path).
- Reason:
  - Root policy treats `cloudbuild.yaml` as deploy truth unless superseded.
  - `pipeline.yaml` is referenced by tests and still useful as a legacy reference.
  - Keeping it without a status marker was misleading.
- Action taken:
  - Added file-level legacy notice and metadata annotations:
    - `mcc.dev/manifest-status=legacy-reference-only`
    - `mcc.dev/authoritative-deploy=cloudbuild.yaml`
  - Added test guard in `tests/test_infra_manifest.py` to keep this status explicit.

## Dependency Hygiene + Vulnerability Inventory
- `deptry` result: `DONE` (tool runs; many mixed `DEP002` findings intentionally not mass-pruned here).
- High-confidence metadata issue: `reportlab` imported directly in `src/services/pdf_writer.py` and previously undeclared.
  - Action: added `reportlab==4.2.0` to `requirements.txt`.

### pip-audit Prioritization (`.venv/bin/pip-audit --local`)
- Result: `Found 25 known vulnerabilities in 10 packages`.
- Runtime-critical candidates (prioritize first):
  - `python-multipart` (`GHSA-wp53-j4wj-2cfg`, fix `0.0.22`)
  - `pypdf` (multiple GHSA IDs, fix chain up to `6.7.4`)
  - `protobuf` (`GHSA-7gcm-g887-7qv7`, fix `5.29.6` / `6.33.5`; constrained by current `<5` policy)
  - `urllib3` (three GHSA IDs, fixes up to `2.6.3`)
  - `orjson` (`GHSA-hx9q-6w63-j58v`, no fix version listed)
- Likely tooling or non-runtime-first:
  - `pip`, `wheel`, `filelock`
- Mixed/depends-on-runtime-path usage:
  - `pillow` (used by PDF/image paths in some stacks; validate usage before remediation)
  - `pyasn1` (mostly auth/crypto stack dependency)

## Files Changed This Pass
- `cloudbuild.yaml`
- `README.md`
- `pipeline.yaml`
- `tests/test_infra_manifest.py`
- `requirements.txt`
- `PLANS.md` (current pass evidence entry)
- `docs/CURRENT_STATE.md` (this file)

## Validation Evidence
- Passed: `.venv/bin/python -m ruff check src tests`
- Passed: `.venv/bin/python -m mypy --strict src`
- Passed: `.venv/bin/python -m pytest --cov=src --cov-branch --cov-report=term-missing`
  - `192 passed, 6 skipped`
  - coverage summary: `Total coverage: 94.91%`
- Passed: `git diff --check`
- Supplemental config checks:
  - `yaml.safe_load('cloudbuild.yaml')`
  - `yaml.safe_load('pipeline.yaml')`

## Risks / Unknowns / Rollback
- Remaining risks:
  - Ingress remains `all` by approved runtime policy; although IAM invoker is currently restricted, this is security-sensitive and should stay under explicit review.
  - `pip-audit` findings are recorded but not remediated in this scoped pass.
  - `protobuf` vulnerability remediation likely conflicts with current `<5` compatibility constraint and needs a separate compatibility task.
- Rollback:
  - Revert this pass commit to restore prior repo config/doc state.
- No cloud writes performed.

## Evidence Log (Commands Run This Pass)
- Branch/bootstrap:
  - `git fetch origin`
  - `git checkout main`
  - `git merge --ff-only origin/main`
  - `git checkout -b codex/feat/config-align-live-runtime`
- Read-first docs:
  - `AGENTS.md`
  - `PLANS.md`
  - `docs/CURRENT_STATE.md`
  - `docs/REFACTOR_RUNBOOK.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CODEBASE_MAP.md`
  - `docs/TESTING.md`
  - `docs/GCP_REFACTOR_PLAN.md`
- Phase 0 verification:
  - `git status --short --branch`
  - `gcloud auth list --format='table(account,status)'`
  - `gcloud config list --format='text(core.project,core.account,compute.region,run.region,workflows.location)'`
  - `gcloud run services describe mcc-ocr-summary --region us-central1 --project quantify-agent --format='yaml(metadata.name,status.url,spec.template.spec.serviceAccountName,spec.template.metadata.annotations,spec.template.spec.containers,metadata.annotations)'`
  - `gcloud run services get-iam-policy mcc-ocr-summary --region us-central1 --project quantify-agent --format='json'`
  - `gcloud run services describe mcc-ocr-summary --region us-central1 --project quantify-agent --format='yaml(metadata.annotations,spec.template.metadata.annotations,spec.template.spec.containerConcurrency,spec.template.spec.timeoutSeconds,spec.template.spec.serviceAccountName,status.url)'`
- Dependency audit:
  - `.venv/bin/python -m deptry .`
  - `.venv/bin/pip-audit --local`
- Validation:
  - `.venv/bin/python -m ruff check src tests`
  - `.venv/bin/python -m mypy --strict src`
  - `.venv/bin/python -m pytest --cov=src --cov-branch --cov-report=term-missing`
  - `git diff --check`
