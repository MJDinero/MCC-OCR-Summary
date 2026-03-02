# docs/REFACTOR_RUNBOOK.md — Step-by-Step Codex Refactor Process
## Purpose
Define the deterministic process Codex follows for this repository.
## Workflow policy
- Use feature branch + PR workflow.
- Use one worktree / one branch / one task thread per distinct change.
- Keep diffs small and scoped.
- Prefer repo-safe work first.
- Treat cloud mutation as HUMAN MUST RUN unless explicitly authorized.
## Phase A — Bootstrap
1. Open the repo root.
2. Read `AGENTS.md`, `PLANS.md`, and all docs referenced there.
3. Record branch and commit in `docs/CURRENT_STATE.md`.
4. Check whether the project `.codex` config and rules are loaded.
### Validation
- Instructions summarized correctly
- Working tree state recorded
- Task scope explicit
### Failure Modes
- Wrong working directory
- Root `AGENTS.md` not loaded
- Project not trusted, causing `.codex` config to be ignored
## Phase B — Read-only audit
1. Verify repo structure and critical files.
2. Run local validation commands from `docs/TESTING.md`.
3. If cloud state matters and credentials exist, run the read-only audit steps from
`docs/GCP_REFACTOR_PLAN.md`.
4. Update `docs/CURRENT_STATE.md`.
### Validation
- Verified facts captured
- Unknowns explicit
- No cloud writes performed
### Failure Modes
- Missing credentials
- Unknown target project

- Conflicting environment evidence
## Phase C — Implementation loop
Repeat until the task is complete or a stop condition is hit.
### Loop steps
1. Select one unresolved item from `PLANS.md`.
2. Write a short implementation plan.
3. Make the smallest safe patch.
4. Run required validation commands.
5. Update:
- `PLANS.md`
- `docs/CURRENT_STATE.md`
- PR/task evidence
6. Decide:
- continue
- stop and escalate
- switch to next item
### Hard caps
- Max 6 implementation iterations per task
- Max 2 no-progress iterations
- Max 1 risk surface per patch unless inseparable
### Validation
- Every iteration closes one acceptance criterion or produces a precise blocker
- Tests run after every non-trivial patch
- Evidence is captured before proceeding
### Failure Modes
- Flaky tests
- Hidden environment dependencies
- Agent broadens scope instead of finishing a bounded item
## Phase D — Evidence packaging
For every completed task produce:
- summary of change
- files changed
- commands run
- test results
- rollback note
- remaining risks / unknowns
- explicit HUMAN MUST RUN items, if any

## Phase E — PR / handoff
1. Ensure branch is clean.
2. Ensure tests relevant to the touched surface passed.
3. Open or update PR.
4. Add review notes and any HUMAN MUST RUN commands separately from code changes.
### Validation
- Clean git status
- Reviewable diff
- Evidence included
### Failure Modes
- Branch contaminated with unrelated changes
- Missing tests
- Unclear rollback
## Stop conditions
Stop and ask for a human decision if:
- production-affecting cloud writes are needed
- secret/IAM/billing/public-access changes are required
- data migration or backfill is required
- target environment is unclear
- bounded loop limits are hit

