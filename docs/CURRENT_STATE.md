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
