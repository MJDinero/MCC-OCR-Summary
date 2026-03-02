# .github/AGENTS.md — shim only
This file is a human/automation shim.
Codex should treat `/AGENTS.md` as the authoritative project instruction file.
Do not duplicate policy here. If this file and `/AGENTS.md` diverge, `/AGENTS.md` wins.
## Purpose
- Preserve backward compatibility for any existing repo workflows that look for
`.github/AGENTS.md`.
- Redirect humans and automations to the actual source of truth.
## Required behavior
- Read `/AGENTS.md`.
- Read `PLANS.md`.
- Read `docs/CURRENT_STATE.md`.
- Do not start a refactor task from this file alone.
## Validation
- Confirm `/AGENTS.md` exists and is newer/more complete than this file.
- Confirm any automation or reviewer references `/AGENTS.md` in logs, prompts, or comments.
## Failure Modes
- If this file grows into a second policy surface, delete the duplicate content and keep only the
redirect.
- If an automation is hard-coded to `.github/AGENTS.md`, update that automation to point to
`/AGENTS.md` or explicitly include both.

