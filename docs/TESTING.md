# docs/TESTING.md — Validation, Gates, and Evidence

## Purpose
Define how changes are validated locally and before merge.

## Core principles
- No vibe-based merges.
- Every non-trivial behavior change gets a direct test or an explicit rationale.
- Touched code must not rely on unverified manual console behavior.
- Do not narrow the test surface to make CI appear healthy.

## Baseline local commands
Run these after every non-trivial change:

```bash
python -m ruff check src tests
python -m mypy --strict src
python -m pytest --cov=src --cov-report=term-missing
```

## Dependency policy checks
When dependency metadata is touched, also run:

```bash
python -m deptry .
pip-audit --local
```

`deptry` policy exceptions are centrally encoded in `pyproject.toml` and must
remain explicit, minimal, and justified.

## API / runtime validation
If routes, startup, config, or deploy behavior changed:
- Verify health endpoint(s).
- Verify ingest status route.
- Verify any changed error path with representative bad input.
- Verify logs or structured output for the touched path.

Suggested route checks:
- GET `/healthz`
- GET `/ingest/status/{job_id}`

## Live large-PDF regression checks
When validating known Drive regression samples, run the human-invoked flow in
`docs/LIVE_REGRESSION_LARGE_PDF.md`.

Minimum contract checks for each response:
- `report_file_id` present and well-formed
- `supervisor_passed=true`
- `request_id` present
- expected file ID set matches captured response files
- scorecard artifact written and reviewed

## Coverage policy
- Measure coverage against `src`, not a narrow subpackage.
- Do not reduce coverage scope or inflate coverage by omission.
- For refactor tasks, the minimum rule is:
  - touched behavior has tests
  - repo-wide coverage command runs successfully
  - before/after coverage is recorded in evidence
- If a numeric threshold is changed, record:
  - old threshold
  - new threshold
  - reason
  - replacement control

## Test pyramid for this repo
Unit
Use for:
- redaction utilities
- storage error handling
- config validation
- workflow/state selection
- token propagation handling

Integration
Use for:
- route wiring
- startup validation
- storage + repository boundaries
- summariser contract boundaries

Smoke
Use for:
- deployed health
- status route
- one end-to-end happy path in staging
- one representative failure path where safe

## Required evidence per task
- commands run
- exit codes or pass/fail result
- failing tests fixed or explicitly deferred
- any new tests added
- any skipped tests explained

## Do not do this
- Do not lower gates silently.
- Do not delete failing tests to get green.
- Do not add broad `xfail`/`skip` markers without explanation.
- Do not claim "covered by existing tests" without naming the test files.

## Validation
- The three baseline commands complete successfully or produce a documented blocker.
- Route/runtime verification is included when relevant.
- Evidence is attached to the task or PR.

## Failure Modes
- If tests require unavailable cloud resources, replace with mocks/stubs for repo-local validation and record the missing staging validation separately.
- If the current repo coverage config is misleading, fix measurement first and only then use the result for quality decisions.
- If validation is flaky, capture the flaky behavior instead of ignoring it.
