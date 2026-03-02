# .codex/AGENTS.md — project-specific automation user guidance
Use this file only when `CODEX_HOME=$(pwd)/.codex` is intentionally set,
or when a project-local Codex automation profile is configured to use this repo’s `.codex`
directory.
## Purpose
Provide extra defaults for automation-style or local-environment Codex runs without polluting
the primary repo policy in `/AGENTS.md`.
## Additional automation defaults
- Prefer non-interactive, deterministic commands where possible.
- For GCP work, default to read-only inventory first.
- If `gcloud` credentials are missing, stop and emit HUMAN MUST RUN instead of guessing.
- Emit concise machine-readable evidence where useful.
- Keep task scope narrow and avoid mixed-discipline patches.
- If a task requires cloud writes, package the exact command as HUMAN MUST RUN and stop
before execution.
## Output discipline
For each completed iteration, emit:
- task id
- files changed
- commands run
- pass/fail summary
- blockers
- next recommended action
## Validation
- Run with `CODEX_HOME=$(pwd)/.codex` and confirm Codex reports this file as part of its
loaded instruction set.
- Confirm behavior is additive to `/AGENTS.md`, not contradictory.
## Failure Modes
- If this file conflicts with `/AGENTS.md`, simplify or delete the conflicting instruction here.
- If the automation user does not set `CODEX_HOME`, this file may not be loaded; rely on
`/AGENTS.md` as the primary control plane.

