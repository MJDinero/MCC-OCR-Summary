# AGENTS.md — MCC OCR Summary (Root)

> 📍 **Save at repo root:** `MCC-OCR-Summary/AGENTS.md`  
> This file governs all DevOps, audit, and repair actions for **GPT-5 Codex** and human maintainers.  
> Follow every directive exactly. Do not override without review.

---

## 🎯 Purpose
Operate and maintain the **MCC OCR Summary** pipeline end-to-end using  
**Cloud Run → Eventarc → Workflows → Document AI → GCS.**

Primary goal:  
Take a PDF → perform OCR (+optional Splitter) → Summarize → Write final PDF to `gs://mcc-output/`.

Codex and developers must keep the build minimal, reproducible, and verifiable.

---

## 🧭 Golden Rules & Guardrails
### 🔒 Security
- All Cloud Run endpoints are private; access via OIDC only.
- All secrets and processor IDs come from **Secret Manager**.
- Never log raw document text, tokens, or Signed URLs.

### 🔁 Idempotency
- Every GCS write uses `ifGenerationMatch`.
- Duplicate deliveries → no-op or HTTP 412.
- No destructive operations.

### ⚙️ Reliability
- Retries = exponential backoff + jitter.  
- Failures logged; DLQ optional but cleanly configured (create-if-missing or removed).

### 🧩 Observability
Emit ordered log markers with `job_id | trace_id | schema_version | duration_ms`:

`ingest_received → split_done → ocr_lro_started → ocr_lro_finished → summary_done → pdf_writer_complete → drive_upload_complete`

---

## ⚙️ Build & Test Pipeline
```bash
ruff check .
mypy --strict src
pytest -q --disable-warnings --maxfail=1 --cov=src --cov-fail-under=85
🚀 Deploy (Cloud Run Gen2)
bash
Copy code
PROJECT_ID="quantify-agent"
REGION="us-central1"
SERVICE="mcc-ocr-summary"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE}:latest"

gcloud builds submit --tag "${IMAGE}"
gcloud run deploy "${SERVICE}" \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --service-account "mcc-orch-sa@${PROJECT_ID}.iam.gserviceaccount.com" \
  --update-env-vars "REGION=${REGION},WORKFLOW_NAME=docai-pipeline,LOG_LEVEL=INFO" \
  --no-allow-unauthenticated
Runtime flags

diff
Copy code
--execution-environment=gen2
--no-cpu-throttling
--concurrency=1
--timeout=900
--cpu=1
--min-instances=0
✅ Acceptance Criteria
Unit tests green; coverage ≥ 85 %.

/internal/jobs/{id}/events → HTTP 200 for each callback.

Workflow state = SUCCEEDED.

Ordered markers present in logs.

Summarized PDF exists and readable in gs://mcc-output/.

🔍 Document AI Audit & Repair Rules
Processor Order
Splitter → OCR → Summarizer → PDF Writer
If no Splitter ID configured, workflow must skip split gracefully and pass objectUri directly to OCR.

Splitter Logic (Workflow YAML)
Guard all references to documentOutputConfig inside hasSplitter branch.

When splitterProcessor == null → set:

yaml
Copy code
splitOutputUri: ${objectUri}
shards:
  - ${objectUri}
DLQ Topic
Preferred:

bash
Copy code
gcloud pubsub topics describe mcc-ocr-pipeline-dlq --format='value(name)' \
  || gcloud pubsub topics create mcc-ocr-pipeline-dlq
Or remove the publishDlq step entirely.

/ingest Endpoint (CloudEvent + JSON)
Update src/api_ingest.py to accept both formats:

python
Copy code
from cloudevents.http import from_http
@app.post("/ingest")
async def ingest(request: Request):
    event = from_http(request.headers, await request.body())
    data = getattr(event, "data", None) or json.loads(await request.body())
    # normalize {bucket,name,objectUri,...}
    return {"status": "ok"}
Add tests for both CloudEvent and plain JSON payloads.

🧾 PR / Change Checklist
 GCS writes guarded by ifGenerationMatch

 Splitter branch null-safe (no KeyError)

 DLQ topic exists or removed cleanly

 /ingest accepts CloudEvent and JSON (422 eliminated)

 Workflow logs show ordered markers

 Lint / type / test clean (≥ 85 % cov)

 No secret data in logs

🧩 Subsystem Notes
Summarizer / PDF Writer
Run inline via Workflows Jobs.
Emit summary_done and pdf_writer_complete.

Workflows / Eventarc
Trigger: google.cloud.storage.object.v1.finalized on mcc-intake.

Flow: Splitter → OCR → Summarizer → PDF Writer.

Env: PIPELINE_STATE_BACKEND, PIPELINE_SERVICE_BASE_URL.

OIDC auth required for callbacks.

IAM Sketch
Component	Roles
Workflow SA	roles/secretmanager.secretAccessor, roles/run.invoker, roles/documentai.apiUser, roles/storage.objectAdmin
Cloud Run SA	roles/workflows.invoker, roles/storage.objectAdmin

🔊 Operational Loop (for Codex Prompt)
Each iteration → Plan → Diff → Commands → Results → Checks → Next

Keep changes minimal and reversible.

Never fabricate output; show actual command results.

Stop when Exit Criteria met and print:

mathematica
Copy code
✅ MCC OCR Summary E2E Pipeline Verified — Cloud Run Green.
🧪 Final Checklist (Must All Be True)
Workflow executes → SUCCEEDED

/internal/jobs/{id}/events → HTTP 200

Ordered markers present in logs

Summarized PDF in gs://mcc-output/

Lint / Type / Tests pass (≥ 85 %)

Final confirmation line logged