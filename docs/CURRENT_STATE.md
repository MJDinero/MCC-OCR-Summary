# docs/CURRENT_STATE.md — Verified Current State Register

Last updated: 2026-03-03 16:54:57 PST
Updated by: Codex (thread: final-hardening-and-regression-orchestration)
Repo branch: `codex/feat/final-hardening-and-regression-orchestration`
Repo commit (branch baseline): `1ba97151432d517c7ef5ba46566acf59ddb3d1c6`
Task id: `final-hardening-and-regression-orchestration`
Target GCP project: `quantify-agent` (canonical target)
Target region: `us-central1` (canonical target)
Cloud audit status: `NOT RUN (repo-local phases only; no cloud writes performed)`

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
