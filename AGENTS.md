# AGENTS.md — MCC-OCR-Summary
Audience: Codex / GPT-5.3-Codex and human engineers.
## Mission
Refactor this repository and its target staging environment to enterprise-grade quality:
- secure by default
- fail closed
- privacy-safe
- reproducible
- test-gated
- least privilege
- evidence-backed
## Read-first order
Always read these files before making changes:
1. PLANS.md
2. docs/CURRENT_STATE.md
3. docs/REFACTOR_RUNBOOK.md
4. docs/ARCHITECTURE.md
5. docs/CODEBASE_MAP.md
6. docs/TESTING.md
7. docs/GCP_REFACTOR_PLAN.md
## Source of truth
- Trust current repo code/config over historical docs.
- Treat `cloudbuild.yaml` as the default deploy truth unless explicitly superseded and validated.
- Treat live GCP state as unknown until verified by a read-only audit.
- Do not treat `.github/AGENTS.md` as authoritative; this root file is authoritative.
## Current starting assumptions (verify at task start)
- The repo contains `cloudbuild.yaml`, `pipeline.yaml`, `src/`, `tests/`, `PLANS.md`,
`PROGRESS.md`, `README.md`, and `REPORT.md`.
- The current implementation is primarily one FastAPI app.
- The current deploy path is one Cloud Build deployment to one Cloud Run service.
- The repo has known risk around privacy/logging, deploy drift, and fail-open orchestration/state.
- The repo may contain older docs or instructions that overstate current hardening.
## Deterministic autonomous loop
Default loop for any non-trivial task:
1. Read the files in the read-first order.
2. Record branch, commit, and task scope in `docs/CURRENT_STATE.md`.
3. Verify repo state with fixed commands from `docs/TESTING.md`.

4. If cloud state matters and is unknown, run only the read-only audit steps from
`docs/GCP_REFACTOR_PLAN.md`.
5. Pick exactly one highest-priority unresolved item from `PLANS.md`.
6. Write a short implementation plan before editing.
7. Make the smallest safe patch that resolves that item.
8. Run the required validation commands.
9. Update evidence:
- what changed
- commands run
- results
- risks
- rollback
10. Update `PLANS.md` and `docs/CURRENT_STATE.md`.
11. Continue only if progress was real and validation improved.
## Bounded loop guardrails
- Maximum 6 implementation iterations per task thread.
- Maximum 2 consecutive no-progress iterations.
- Maximum 1 risk surface per patch/PR unless the files are inseparable.
- If two iterations fail to improve the same acceptance criterion, stop and escalate.
- Never end with "in progress" plan items; mark each item Done, Blocked, or Cancelled.
## Hard limits
- No direct-to-main refactor work.
- Use feature branch + PR workflow unless a human explicitly overrides.
- No production writes without explicit approval.
- No destructive cloud changes without explicit approval.
- No IAM changes, secret creation/rotation, billing changes, or public-access changes without
HUMAN MUST RUN.
- No committing secrets, key-like fixtures, PHI, or raw customer artifacts.
- No broad "cleanup" edits without tests.
- No lowering gates to force green unless the replacement control is documented and approved.
- No destructive git commands (`git reset --hard`, force-push, branch deletion) without explicit
approval.
## Coding rules
- Optimize for correctness, clarity, and reliability over speed.
- Conform to existing project conventions unless there is a documented reason to change them.
- Prefer small diffs and incremental patches.
- Prefer deterministic commands and scripts over ad hoc shell experiments.
- Prefer ASCII unless a file already requires Unicode.
- Prefer explicit error handling over silent fallbacks.
- Keep comments rare and useful.
- For current/fresh vendor or OpenAI behavior, use web search only when freshness is material.

## Cloud rules
- Read-only GCP audit is allowed if credentials already exist and the target project is known.
- Any write action to GCP infrastructure is HUMAN MUST RUN unless the human explicitly
authorizes that specific command category.
- Prefer service account impersonation over downloaded JSON keys.
- Never assume the intended target project/region; record it in `docs/CURRENT_STATE.md`.
## Required validation commands
Run the relevant commands after every non-trivial change:
- `python -m ruff check src tests`
- `python -m mypy --strict src`
- `python -m pytest --cov=src --cov-report=term-missing`
If touching routes, deploy, or runtime behavior, also run:
- route/health verification
- smoke verification
- config validation
- any additional checks listed in `docs/TESTING.md`
## Required task output
Every completed task must include:
- summary of what changed
- files changed
- commands run
- test results
- rollback plan
- remaining risks / unknowns
- whether `PLANS.md` and `docs/CURRENT_STATE.md` were updated
## Stop conditions
Stop and ask for confirmation if:
- a cloud change would affect production
- a credential rotation is needed
- target GCP project or region is unclear
- a change requires a data migration or backfill
- validation cannot be completed with current access
- you hit the bounded loop limits above
## Validation
- Start Codex in the repo root and ask it to summarize current instructions.
- Confirm it reads this file plus the docs listed above.
- Confirm task execution follows the bounded loop and test commands.

## Failure modes
- If root `AGENTS.md` is ignored, verify Codex was started in the repo root and the project is
trusted.
- If cloud state is ambiguous, stop repo changes that depend on cloud truth and do the
read-only audit first.
- If validation commands fail for environmental reasons, document the blocker and produce a
minimal reproducible setup fix.

