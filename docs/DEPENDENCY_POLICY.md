# Dependency Policy

## Purpose
Define authoritative dependency sources and explicit exception handling rules.

## Authoritative Files
- Runtime and image dependencies: `requirements.txt`
- Development/test/tooling dependencies: `requirements-dev.txt`
- Version guardrails: `constraints.txt`

`requirements.lock` is retired and not part of active local, CI, or deploy
workflows.

## deptry Policy
`deptry` configuration is encoded in `pyproject.toml`.

Current intentional `DEP002` exceptions:
- `pillow`
  - direct pin used as a security floor for report/PDF rendering dependency
    hygiene.
- `python-multipart`
  - runtime-required for FastAPI multipart file upload parsing despite no direct
    import in application modules.

Rule: new exceptions require explicit rationale in this file and corresponding
`pyproject.toml` updates.

## Update Workflow
1. Edit dependency files (`requirements*.txt`, `constraints.txt`) in a scoped change.
2. Install and validate in the repo virtual environment.
3. Run:
   - `python -m deptry .`
   - `pip-audit --local`
   - baseline lint/type/test gates from `docs/TESTING.md`
4. Record results and risk/rollback notes in `docs/CURRENT_STATE.md` and `PLANS.md`.
