# MCC-OCR Summary — Execution Plan

## Phased Task List

1. **Phase 1 – CMEK Enforcement & Propagation**
   1.1 Audit existing GCS/BQ/DocAI resources for CMEK configuration gaps.  
   1.2 Update `pipeline.yaml`, Cloud Build substitutions, and runtime configs to inject `CMEK_KEY_NAME`.  
   1.3 Modify DocAI request builders and storage writers to pass `encryption_key_name`.  
   1.4 Add validation scripts/tests (`gsutil stat`, `bq show`) proving CMEK coverage.

2. **Phase 2 – Secrets & Auth Hardening**
   2.1 Replace inline env usage with Secret Manager resolution utility (runtime + Cloud Build).  
   2.2 Enforce `X-Internal-Event-Token` header across `/internal/jobs/*`; add 401/200 tests.  
   2.3 Remove `sm://` placeholders from sample envs; document secret sourcing.

3. **Phase 3 – CI/CD Pipeline Hardening**
   3.1 Update `cloudbuild.yaml` to deploy by digest, add SBOM + provenance + scanning + cosign signing.  
   3.2 Publish security artifacts to CMEK-backed bucket; wire pipeline env/secret flags.  
   3.3 Ensure Cloud Run deployment uses stage-scoped service accounts and no unauthenticated flag.

4. **Phase 4 – IAM Least Privilege Refactor**
   4.1 Define stage-specific service accounts and scope bindings in `infra/iam.sh`.  
   4.2 Limit Cloud Build SA impersonation to required runtimes only.  
   4.3 Document IAM updates + rollout procedure.

5. **Phase 5 – Observability & Monitoring**
   5.1 Instrument Prometheus metrics in services; expose `/metrics`.  
   5.2 Enhance structured logging schema, ensure redaction flag propagation.  
   5.3 Add dashboards (`infra/monitoring/dashboard_*.json`) and alert policies with apply scripts.

6. **Phase 6 – Testing & Quality Gates**
   6.1 Expand test suite to cover CMEK, Secret Manager, `/internal/jobs/*`, `/metrics`.  
   6.2 Achieve ≥90% coverage; publish coverage artifacts to CMEK bucket.

7. **Phase 7 – Runtime & Performance Tuning**
   7.1 Right-size Uvicorn workers/concurrency; add autoscaling + caching + temp cleanup.  
   7.2 Record resource metrics in logs/metrics and verify via smoke test.

8. **Phase 8 – Static Analysis & Code Quality**
   8.1 Enable strict `mypy`, ruff, pylint rules; create `make lint`, `make type`, `make audit-deps`.  
   8.2 Ensure CI gating on new quality targets.

9. **Phase 9 – Documentation & Compliance**
   9.1 Maintain `PLAN.md`, `DISCOVERY.md`, `REPORT.md`, `BLOCKERS.md` as work progresses.  
   9.2 Update README/runbooks with SLOs, dashboards, rollback, and compliance posture.  
   9.3 Commit `scripts/e2e_smoke.sh` outputs + instructions.

10. **Phase 10 – Verification & Acceptance**
    10.1 Run full regression (lint/type/tests/smoke).  
    10.2 Capture evidence for REPORT.md (CMEK stats, auth probes, metrics output).  
    10.3 Validate Cloud Build deploys by digest, secrets from Secret Manager, no unauth access.

11. **Phase 11 – End-to-End Smoke Test**
    11.1 Finalize `scripts/e2e_smoke.sh` to validate ingest → summary → storage with CMEK + auth + metrics.  
    11.2 Store PASS/FAIL summary, timings, and artifacts in CMEK bucket.  
    11.3 Document acceptance criteria sign-off.

## Dependency Graph

```
Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6 → Phase 7 → Phase 8 → Phase 9 → Phase 10 → Phase 11
            │                           │                      │
            └───────────────┐           └──────────────┐       └─────┐
                            v                            v            v
                       Phase 5 metrics feeds       Phase 6 tests   Phase 9 docs
```

_Execution principle: roll forward only, capturing evidence after each phase._
