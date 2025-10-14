#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="mcc-ocr-summary"
REGION="${REGION:-us-central1}"
PROJECT="${PROJECT_ID:?PROJECT_ID env var required}"
IMAGE="gcr.io/$PROJECT/$SERVICE_NAME:$(git rev-parse --short HEAD)"
APP_VERSION="v11h-$(git rev-parse --short HEAD)"

echo "Building image: $IMAGE" >&2
gcloud builds submit --tag "$IMAGE" .

echo "Deploying to Cloud Run: $SERVICE_NAME" >&2
gcloud run deploy "$SERVICE_NAME" \
  --image "$IMAGE" \
  --region "$REGION" \
  --platform managed \
  --allow-unauthenticated \
  --project "$PROJECT" \
  --timeout=600 \
  --memory=1Gi \
  --set-env-vars PROJECT_ID="$PROJECT" \
  --set-env-vars DOC_AI_LOCATION="${DOC_AI_LOCATION:-us}" \
  --set-env-vars DOC_AI_OCR_PROCESSOR_ID="${DOC_AI_OCR_PROCESSOR_ID:-}" \
  --set-env-vars OPENAI_API_KEY="${OPENAI_API_KEY:-}" \
  --set-env-vars OPENAI_MODEL="${OPENAI_MODEL:-}" \
  --set-env-vars USE_STRUCTURED_SUMMARISER="${USE_STRUCTURED_SUMMARISER:-true}" \
  --set-env-vars APP_VERSION="${APP_VERSION}" \
  --set-env-vars ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-*}" \
  --set-env-vars STUB_MODE="${STUB_MODE:-false}" \
  --set-env-vars DOC_AI_FORM_PARSER_ID="${DOC_AI_FORM_PARSER_ID:-}" \
  --set-env-vars DOC_AI_INVOICE_PROCESSOR="${DOC_AI_INVOICE_PROCESSOR:-}" \
  --set-env-vars DOC_AI_SPLITTER_PROCESSOR="${DOC_AI_SPLITTER_PROCESSOR:-}" \
  --set-env-vars DOC_AI_CLASSIFIER_PROCESSOR="${DOC_AI_CLASSIFIER_PROCESSOR:-}" \
  --set-env-vars DRIVE_INPUT_FOLDER_ID="${DRIVE_INPUT_FOLDER_ID:-}" \
  --set-env-vars DRIVE_REPORT_FOLDER_ID="${DRIVE_REPORT_FOLDER_ID:-}" \
  --set-env-vars SHEET_ID="${SHEET_ID:-}" \
  --set-env-vars ARTIFACT_BUCKET="${ARTIFACT_BUCKET:-}" \
  --set-env-vars CLAIM_LOG_SALT="${CLAIM_LOG_SALT:-}" \
  --set-env-vars SHEET_TAB_GID="${SHEET_TAB_GID:-}" \
  --set-env-vars WRITE_TO_DRIVE="${WRITE_TO_DRIVE:-false}" \
  --set-env-vars DRIVE_ENABLED="${DRIVE_ENABLED:-false}" \
  --set-env-vars SHEETS_ENABLED="${SHEETS_ENABLED:-false}" \
  --set-env-vars PDF_ENABLED="${PDF_ENABLED:-false}" \
  --set-env-vars FULL_PROCESSING="${FULL_PROCESSING:-true}" \
  --command uvicorn --args "src.main:create_app --factory --host 0.0.0.0 --port 8080"

echo "Deployment complete." >&2
