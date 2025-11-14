# Hierarchical Patch v11k — Output & Timeout Remediation

## Findings
- `/process_drive` requests stalled until Cloud Run timed out. Investigation showed the entire summarisation → PDF → Drive pipeline executed inline on the FastAPI event loop, so any slow Drive upload or PDF rendering blocked the response indefinitely.
- The OpenAI summariser completed successfully (multiple `200 OK` responses in logs), but no downstream markers (`pdf_writer_complete`, `Drive upload complete`) appeared, confirming the hang occurred after summarisation.
- The legacy `PDFWriter` offered minimal instrumentation and performed no validation on returned bytes, making it difficult to detect backend failures.
- Drive helper functions emitted generic logs and offered no insight into download/upload durations.

## Remediation Summary
- Re-architected the service around an event-driven ingestion model:
  - Added `/ingest` and `/tasks/pipeline` endpoints. `/ingest` enqueues to Pub/Sub (when configured) or executes inline for local testing. `/tasks/pipeline` processes Pub/Sub push messages.
  - Introduced `src/services/pipeline.py`, a reusable orchestration layer that stages input to GCS, runs OCR, summarisation, validation, PDF generation, and uploads the result to Drive/GCS with structured logging (`pipeline_started`, `gcs_summary_upload_success`, `drive_upload_complete`).
- Added `src/services/pdf_writer_refactored.py`, a hardened writer that:
  - Reuses the proven minimal backend but wraps outputs with deterministic text wrapping.
  - Validates `%PDF-` headers / `%%EOF` trailers and emits structured logging for start/finish.
  - Guarantees byte payloads suitable for Drive uploads.
- Updated OpenAI summariser integration to the current Responses API contract by supplying `text={"format": {"type": "json_object"}}` and reading structured results from `response.output[..].content[..].text`, removing the deprecated `response_format` usage.
- Overhauled `src/main.py`:
  - Default to the refactored PDF writer (`USE_REFACTORED_PDF_WRITER`) and structured summariser.
  - Added asynchronous ingestion helpers with Pub/Sub publishing (`PIPELINE_PUBSUB_TOPIC`) and inline fallbacks (`RUN_PIPELINE_INLINE`).
  - `/process_drive` now runs through the shared pipeline (inline for legacy compatibility) while `/ingest` is the preferred trigger.
- Simplified `src/services/docai_helper.py` to rely on Document AI batch processing for large documents (auto-splitting) and removed the PyPDF-based splitter (`src/utils/pdf_splitter.py` now raises `NotImplementedError` with guidance to configure the Document AI Splitter processor).
- Enhanced `src/services/drive_client.py` logging for download/upload lifecycle visibility.
- Added/updated tests (`tests/test_pipeline_integration.py`, `tests/test_pipeline_endpoints.py`, `tests/test_large_pdf_split_integration.py`) to cover the new pipeline entry points and Document AI batch flow.

## Validation
- `.venv/bin/pytest -q --maxfail=1`
- `python3 -m src.services.summariser_refactored --dry-run --input tests/fixtures/sample_ocr.json --output outputs/test_summary.json`

## Deployment
Deploy patched image (`v11k-outputfix`) with canonical URL only:

```bash
gcloud run deploy mcc-ocr-summary \
  --image=gcr.io/$PROJECT_ID/mcc-ocr-summary:v11k-outputfix \
  --region=us-central1 \
  --platform=managed \
  --no-allow-unauthenticated \
  --no-traffic \
  --set-env-vars=ENABLE_PDF_WRITER=true,USE_REFACTORED_PDF_WRITER=true,LOG_LEVEL=DEBUG,RUN_PIPELINE_INLINE=false
gcloud run services update-traffic mcc-ocr-summary --region=us-central1 --to-latest
```

After deployment, confirm Cloud Run logs show the full sequence:
`summariser_generation_complete → supervisor_validation_started → pdf_writer_complete → drive_upload_complete`
and verify the PDF in `gs://demo-gcp-project-summary-artifacts/output/`.
