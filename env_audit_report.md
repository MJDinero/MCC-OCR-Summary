# Environment Audit – Phase 1

## Scope
Validated presence and consistency of critical runtime variables across local development (.env template), CI (GitHub Actions), and deployment tooling (Cloud Build & deploy script). Updated configuration to ensure Cloud Run uses Secret Manager mounted credentials at `/secrets/mcc_orch_sa_key.json`.

## Key Variables
| Variable | Local (.env.template) | CI (.github/workflows/ci.yml) | Cloud (cloudbuild.yaml / scripts/deploy.sh) |
| --- | --- | --- | --- |
| PROJECT_ID | quantify-agent | test-project | quantify-agent |
| REGION | us-central1 | us | us-central1 |
| DOC_AI_PROCESSOR_ID | 21c8becfabc49de6 | dummy | 21c8becfabc49de6 |
| DRIVE_INPUT_FOLDER_ID | 19xdu6hV9KNgnE_Slt4ogrJdASWXZb5gl | drive-in | 19xdu6hV9KNgnE_Slt4ogrJdASWXZb5gl |
| DRIVE_REPORT_FOLDER_ID | 1eyMO0126VfLBK3bBQEpWlVOL6tWxriCE | drive-out | 1eyMO0126VfLBK3bBQEpWlVOL6tWxriCE |
| DRIVE_SHARED_DRIVE_ID | 0AFPP3mbSAh_oUk9PVA | 0AFPP3mbSAh_oUk9PVA | 0AFPP3mbSAh_oUk9PVA |
| DRIVE_IMPERSONATION_USER | Matt@moneymediausa.com | impersonation@example.com | Matt@moneymediausa.com |
| MODE | mvp | mvp | mvp |
| STUB_MODE | false | true | false |
| WRITE_TO_DRIVE | true | false | true |
| GOOGLE_APPLICATION_CREDENTIALS | /secrets/mcc_orch_sa_key.json | /tmp/test-service-account.json | /secrets/mcc_orch_sa_key.json |
| SERVICE_ACCOUNT_JSON | sm://mcc_orch_sa_key | {} | Secret Manager (env & file mount) |

## Remediation Actions
- Standardised credential path to `/secrets/mcc_orch_sa_key.json` across README, templates, Cloud Build, and deployment script.
- Replaced all `--set-env-vars` invocations with `--update-env-vars` for idempotent deployments.
- Ensured `STUB_MODE` and `WRITE_TO_DRIVE` explicitly propagate to Cloud Run and CI to match runtime expectations.
- Added Secret Manager file mount (`/secrets/mcc_orch_sa_key.json`) alongside the existing `SERVICE_ACCOUNT_JSON` env secret for Cloud Run revisions.
- Documented canonical Drive folder IDs to eliminate lowercase `l` vs uppercase `I` ambiguity.

## Outstanding Checks
- Confirm Secret Manager mount succeeds in staging revision (will verify during deployment dry-run in later phases).
- Validate CI stub mode coverage keeps integration tests hermetic once DocAI checks are reintroduced in Phase 4.
