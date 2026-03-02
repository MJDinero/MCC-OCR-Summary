# docs/GCP_REFACTOR_PLAN.md — Plan-Only, Staging-First GCP Refactor
## Purpose
Provide a safe, plan-only path for aligning the repository with one clean staging GCP
environment.
## Scope policy
- This document may contain read-only audit steps and HUMAN MUST RUN write steps.
- Codex must not execute destructive or privileged cloud mutations from this plan without explicit
human approval.
- The default objective is one clean staging project, not simultaneous repair of multiple drifting
environments.
## Assumptions
- Target project: UNKNOWN until human sets it
- Target region: UNKNOWN until human sets it
- v1 runtime target: Cloud Run primary service, with existing async GCP integrations only where
justified
- Workflow: feature branch + PR
## Phase 0 — Human selects the target staging environment
### HUMAN MUST RUN
- Choose `PROJECT_ID`
- Choose `REGION`
- Record both in `docs/CURRENT_STATE.md`
### Validation
- Human confirms target values in writing
- `docs/CURRENT_STATE.md` updated
### Failure Modes
- If multiple GCP projects are “possibly current,” stop and require a human naming decision
## Phase 1 — Read-only GCP audit
If local `gcloud` access exists and the target is known, Codex may run read-only inventory
commands.
### Suggested read-only inventory
- active account / active project
- enabled services
- Cloud Run services
- Artifact Registry repositories
- buckets

- Secret Manager metadata (names/versions only; no secret values)
- service accounts
- IAM bindings
- Workflows / Eventarc / Pub/Sub
- KMS keyrings/keys metadata
- BigQuery datasets/tables metadata
### Output
- update `docs/CURRENT_STATE.md`
- list repo-vs-cloud mismatches
- list unknowns that block safe refactor
### Validation
- No write commands executed
- Audit results recorded with timestamp
### Failure Modes
- auth missing
- wrong active project
- permissions insufficient for read-only inventory
## Phase 2 — Normalize repo configuration for one staging target
### Codex may do
- prepare config refactors in code and deploy files
- replace hard-coded environment assumptions with placeholders or documented variables
- prepare HUMAN MUST RUN command lists
### Codex must not do without approval
- create or delete buckets
- create or rotate secrets
- change IAM bindings
- enable/disable billing
- expose services publicly
### Validation
- proposed config diff is reviewable
- required variables are documented
- HUMAN MUST RUN list is explicit
### Failure Modes
- agent tries to “fix” the wrong environment
- hidden environment assumptions remain in deploy files
## Phase 3 — Human-provisioned staging resources

### HUMAN MUST RUN
Provision the minimum resources required for staging:
- project-level APIs/services
- artifact registry
- storage buckets
- KMS resources if used
- service accounts
- secrets
- workflow/event resources if actually required
- BigQuery dataset/table resources if actually required
### Validation
- human records what was created
- identifiers are added to `docs/CURRENT_STATE.md`
- repo config is updated only after values are verified
### Failure Modes
- manual console changes not recorded
- over-broad IAM grants
- secrets created without names/owners documented
## Phase 4 — Staging deploy and verification
### Codex may do
- validate repo-local deploy config
- prepare commands and checklists
- verify outputs after a human-approved deploy
### HUMAN MUST RUN
- actual staging deploy if it requires cloud writes or privileged access
### Validation
- service reachable on intended private path
- health/status checks pass
- logs/metrics checked
- any changed route or failure path verified
### Failure Modes
- deploy succeeds but runtime config is wrong
- metrics are dark
- service exposure is broader than intended
## Phase 5 — Migration / cleanup decision
Only after staging is reproducible:
- freeze old environments

- choose migrate / retire / keep as read-only reference
- do not clean up multiple legacy environments mid-refactor
## Golden rules
- one target staging environment
- no secret values in docs
- no console-only fixes without recording them
- prefer human-reviewed, reproducible commands over one-off UI edits
## Validation
- Every completed phase has evidence
- `docs/CURRENT_STATE.md` is current
- HUMAN MUST RUN items are explicit and separate from repo-safe work
## Failure Modes
- target environment ambiguity
- privilege gaps
- undocumented manual fixes
- agent overreaching into cloud mutation

