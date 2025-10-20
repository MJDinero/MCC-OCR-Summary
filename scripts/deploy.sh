#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="mcc-ocr-summary"
REGION="${REGION:-us-central1}"
PROJECT="${PROJECT_ID:?PROJECT_ID env var required}"
IMAGE="gcr.io/$PROJECT/$SERVICE_NAME:$(git rev-parse --short HEAD)"
APP_VERSION="v11h-$(git rev-parse --short HEAD)"
MODE_VALUE="${MODE:-mvp}"
DOC_AI_LOCATION="${DOC_AI_LOCATION:-us}"
DOC_AI_PROCESSOR_ID="${DOC_AI_PROCESSOR_ID:?DOC_AI_PROCESSOR_ID env var required}"
DRIVE_INPUT_FOLDER_ID="${DRIVE_INPUT_FOLDER_ID:?DRIVE_INPUT_FOLDER_ID env var required}"
DRIVE_REPORT_FOLDER_ID="${DRIVE_REPORT_FOLDER_ID:?DRIVE_REPORT_FOLDER_ID env var required}"
DRIVE_SHARED_DRIVE_ID="${DRIVE_SHARED_DRIVE_ID:?DRIVE_SHARED_DRIVE_ID env var required}"
DRIVE_IMPERSONATION_USER="${DRIVE_IMPERSONATION_USER:?DRIVE_IMPERSONATION_USER env var required}"
CMEK_KEY_NAME="${CMEK_KEY_NAME:?CMEK_KEY_NAME env var required}"
GOOGLE_APPLICATION_CREDENTIALS="${GOOGLE_APPLICATION_CREDENTIALS:-/tmp/google-application-credentials.json}"
INTAKE_GCS_BUCKET="${INTAKE_GCS_BUCKET:?INTAKE_GCS_BUCKET env var required}"
OUTPUT_GCS_BUCKET="${OUTPUT_GCS_BUCKET:?OUTPUT_GCS_BUCKET env var required}"
SUMMARY_BUCKET="${SUMMARY_BUCKET:?SUMMARY_BUCKET env var required}"

echo "Building image: $IMAGE" >&2
gcloud builds submit --tag "$IMAGE" .

echo "Deploying to Cloud Run: $SERVICE_NAME" >&2
gcloud run deploy "$SERVICE_NAME" \
  --image "$IMAGE" \
  --region "$REGION" \
  --platform managed \
  --no-allow-unauthenticated \
  --project "$PROJECT" \
  --timeout=600 \
  --memory=1Gi \
  --set-env-vars MODE="$MODE_VALUE" \
  --set-env-vars PROJECT_ID="$PROJECT" \
  --set-env-vars REGION="$REGION" \
  --set-env-vars DOC_AI_LOCATION="$DOC_AI_LOCATION" \
  --set-env-vars DOC_AI_PROCESSOR_ID="$DOC_AI_PROCESSOR_ID" \
  --set-env-vars DRIVE_INPUT_FOLDER_ID="$DRIVE_INPUT_FOLDER_ID" \
  --set-env-vars DRIVE_REPORT_FOLDER_ID="$DRIVE_REPORT_FOLDER_ID" \
  --set-env-vars DRIVE_SHARED_DRIVE_ID="$DRIVE_SHARED_DRIVE_ID" \
  --set-env-vars DRIVE_IMPERSONATION_USER="$DRIVE_IMPERSONATION_USER" \
  --set-env-vars CMEK_KEY_NAME="$CMEK_KEY_NAME" \
  --set-env-vars GOOGLE_APPLICATION_CREDENTIALS="$GOOGLE_APPLICATION_CREDENTIALS" \
  --set-env-vars INTAKE_GCS_BUCKET="$INTAKE_GCS_BUCKET" \
  --set-env-vars OUTPUT_GCS_BUCKET="$OUTPUT_GCS_BUCKET" \
  --set-env-vars SUMMARY_BUCKET="$SUMMARY_BUCKET" \
  --set-env-vars APP_VERSION="${APP_VERSION}" \
  --set-env-vars USE_STRUCTURED_SUMMARISER="${USE_STRUCTURED_SUMMARISER:-true}" \
  --update-secrets=OPENAI_API_KEY=OPENAI_API_KEY:latest,INTERNAL_EVENT_TOKEN=internal-event-token:latest,SERVICE_ACCOUNT_JSON=mcc_orch_sa_key:latest \
  --command uvicorn --args "src.main:create_app --factory --host 0.0.0.0 --port 8080"

echo "Deployment complete." >&2
