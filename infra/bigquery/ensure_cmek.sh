#!/usr/bin/env bash

# Idempotently enforce CMEK on BigQuery dataset and table used by MCC OCR Summary.
# Requires `bq` CLI with appropriate IAM permissions.

set -euo pipefail

: "${PROJECT_ID:?PROJECT_ID env var required}"
: "${SUMMARY_BIGQUERY_DATASET:?SUMMARY_BIGQUERY_DATASET env var required}"
: "${SUMMARY_BIGQUERY_TABLE:?SUMMARY_BIGQUERY_TABLE env var required}"
: "${CMEK_KEY_NAME:?CMEK_KEY_NAME env var required}"
: "${REGION:?REGION env var required}"

log() {
  printf '[ensure_bq_cmek] %s\n' "$*"
}

log "Applying dataset-level CMEK policy"
bq --project_id="${PROJECT_ID}" --location="${REGION}" update \
  --set_kms_key="${CMEK_KEY_NAME}" \
  "${PROJECT_ID}:${SUMMARY_BIGQUERY_DATASET}"

log "Applying table-level CMEK policy"
bq --project_id="${PROJECT_ID}" --location="${REGION}" update \
  --table \
  --set_kms_key="${CMEK_KEY_NAME}" \
  "${PROJECT_ID}:${SUMMARY_BIGQUERY_DATASET}.${SUMMARY_BIGQUERY_TABLE}"

log "BigQuery CMEK enforcement complete."
