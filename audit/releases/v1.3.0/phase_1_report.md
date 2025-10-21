# Phase 1 Report – Environment & Secrets Alignment

## Activities
- Reviewed `.env.template`, `cloudbuild.yaml`, `scripts/deploy.sh`, `README.md`, and CI workflow to catalogue required variables.
- Normalised Cloud Run credential path to `/secrets/mcc_orch_sa_key.json`, adding Secret Manager file mount for the `mcc_orch_sa_key` secret.
- Replaced legacy `--set-env-vars` flags with `--update-env-vars` to avoid accidental deletions of pre-existing variables during redeploys.
- Propagated `MODE`, `STUB_MODE`, and `WRITE_TO_DRIVE` across local, CI, and Cloud Run configurations.
- Corrected `DRIVE_REPORT_FOLDER_ID` to canonical `1eyMO0126VfLBK3bBQEpWlVOL6tWxriCE` wherever deployment automation referenced outdated values.
- Added CI fallbacks for Google credential variables to support hermetic testing without production secrets.

## Validation
- `rg` search confirms no remaining runtime scripts use `--set-env-vars`.
- `git diff` shows consistent credential path updates across docs and automation.
- Manual audit captured in `env_audit_report.md` summarises variable coverage.

## Next Steps
- Phase 2 will exercise Drive credential impersonation using the updated secret mount and enforce stricter validation in `AppConfig`.
