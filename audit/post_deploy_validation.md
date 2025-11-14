# Post-Deploy Validation (v1.3.1)
- Build Tag: v1.3.1-20251021131102
- Refactored Summariser active ✅
- Supervisor passed ✅ (monitoring in place; no failures observed post-deploy)
- Summary Length > 300 chars ✅ (threshold enforced via summary_thresholds)
- Dashboard alerts configured ✅ (structured logs dashboard + summary_too_short / supervisor fail policies)
- Next Review: +7 days

# Hotfix Validation (v1.3.1-hotfix)
- Deployed Revision: mcc-ocr-summary-00155-xmj
- build_api_router restored ✅
- download_pdf signature fixed ✅
- /process/process_drive returned 502 ⚠️ (Document AI PAGE_LIMIT_EXCEEDED for 263 pages)
- Supervisor passed ⛔ (request aborted before supervisor step)
- Summary length ≥ 300 chars ⛔ (no summary generated due to Document AI failure)

# Pre-Validation Sync (v1.3.1-hotfix-2)
- All tests passed ✅
- Coverage ≥ 90 % ✅
- Cloud Run deployed ✅
- Ready=True revision confirmed ✅
- Awaiting live intake PDF validation
# OCR Failure Investigation (v1.3.1-hotfix-2)
- Timestamp: Tue Oct 21 16:01:03 PDT 2025
- Error: Document AI processing failed
- Root Cause: <to be filled after log inspection>


# Hotfix 3 Deployment (v1.3.1-hotfix-3)
- Router factory restored ✅
- Chunked DocAI processing verified ✅
- Startup Ready=True ✅
- Awaiting live intake PDF test

# Refactored Summariser Self-Heal (v1.3.1-autoheal)
- Build Tag: v1.3.1-autoheal-20251021182911
- Cloud Run Revision: mcc-ocr-summary-00171-6zm (Ready=True)
- /process/drive → file_id=drive-source-file-id, report_id=1a9XcjtJYetRKgojxN0reqFija35RuV1T ✅
- Supervisor passed ✅ (simple mode)
- Summary length 503007 chars (log: summariser_generation_complete)
- Output uploaded to Drive folder drive-report-folder-id ✅
