# docs/CURRENT_STATE.md — Verified Current State Register

Last updated: [YYYY-MM-DD]
Updated by: [human or Codex thread]
Repo branch: [branch]
Repo commit: [commit SHA]
Target GCP project: [project-id or UNKNOWN]
Target region: [region or UNKNOWN]

## How to use this file
This is the current-state register.
Do not trust historical notes over this file once it has been refreshed for the active task.

## Required first-pass verification
At the start of every non-trivial task, confirm:
- branch and commit
- current working tree cleanliness
- key entrypoints and deploy artifacts
- whether the target GCP project/region is known
- whether a read-only GCP audit was completed for this task

## Repo facts to verify first
- Root deploy artifacts: `cloudbuild.yaml`, `pipeline.yaml`

- Planning/docs artifacts: `PLANS.md`, `PROGRESS.md`, `README.md`, `REPORT.md`
- App entrypoint: `src/main.py`
- API surfaces: `/ingest`, `/process`, `/healthz`
- Risk hotspots: `src/services/storage_service.py`, `src/services/pipeline.py`, `src/api/ingest.py`,
`.github/workflows/ci.yml`, `infra/iam.sh`

## Carry-forward risk register (re-verify before clearing)
### P0
- Privacy/logging failure paths
- Fail-open orchestration/state outside local/dev
- Secret exposure surface in orchestration paths
- Automated deploy posture drift

### P1
- CI truthfulness and coverage scope
- Observability/deploy mismatch
- IAM breadth
- Config drift and duplicate paths

## Unknowns
Record each open question here as:
- Question:
- Why it matters:
- How to verify:
- Status: Open / Verified / Blocked

## Evidence log
For each task append:
- Task:
- Verified facts:
- Commands run:
- Result:
- Remaining unknowns:

## Validation
- Repo branch and commit recorded
- Target project/region recorded or explicitly marked UNKNOWN
- Read-only cloud audit status recorded
- Verified facts separated from assumptions

## Failure Modes
- If this file is stale or incomplete, do not proceed with cloud-dependent changes.
- If facts conflict, preserve both, mark the conflict explicitly, and verify before coding.
- If the target GCP project is unknown, do not perform cloud writes.

