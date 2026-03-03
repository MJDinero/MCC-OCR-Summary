# MCC-OCR-Summary — v11k Hierarchical Patch Report

## Root Cause
- Cloud Run revision `v11k-hierarchical` crashed before PDF generation because the `OpenAIResponsesBackend` passed `response_format=…` to `client.responses.create`.  
- OpenAI SDK `1.93.3` removed that keyword from the Responses API, so the call raised `TypeError: unexpected keyword argument 'response_format'`, aborting summarisation and skipping downstream supervisor/PDF stages.

## Remediation
- Updated `RefactoredSummariser` to request JSON mode via the supported `text={"format": {"type": "json_object"}}` parameter instead of the deprecated `response_format`.  
- Re-ran local dry-run to confirm the hierarchical summariser still yields multi-section output above supervisor thresholds (>400 chars).  
- Existing PDF writer instrumentation (`pdf_writer_started` / `pdf_writer_complete`) remains intact to verify downstream stages post-deployment.

## Validation
- `.venv/bin/pytest -q --maxfail=1`  
- `python3 -m src.services.summariser_refactored --dry-run --input tests/fixtures/sample_ocr.json --output outputs/test_summary.json`

## Deployment & Next Steps
1. Build and deploy (`v11k-patch`) to Cloud Run.  
2. Trigger `/process_drive` for a staging file.  
3. Confirm logs show the full sequence: `summariser_generation_complete` → `supervisor_validation_started` → `pdf_writer_complete` → `Drive upload complete`.  
4. Verify artifact presence in `gs://quantify-agent-mcc-phi-artifacts/output/`.

No further runtime errors are expected once the patched revision is deployed.
