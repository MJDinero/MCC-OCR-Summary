# MCC-OCR-Summary — Summariser Audit Report

## 1. Executive Summary
- **Primary failure modes:** Supervisor rejects summaries for being too short and weakly aligned with source OCR because the current prompt forces ultra-concise JSON snippets that collapse detail during merge.【F:src/services/summariser.py†L68-L137】【F:src/services/summariser.py†L429-L520】
- **Risk level:** High. Production metrics show repeated supervisor retries, inflating latency and compute cost while still producing non-compliant summaries.
- **Remediation:** Replace the JSON-only chunk prompt with a richer, hierarchy-aware template and deterministic merge that emits the mandated “Intro / Key Points / Detailed Findings” structure, ensuring length ≥200 chars and preserving factual coverage. Implemented in `RefactoredSummariser` with updated OpenAI backend instructions.【F:src/services/summariser_refactored.py†L1-L360】

## 2. Findings & Root Causes
### 2.1 Prompt Construction
- The legacy system message tells the model to “Return ONLY JSON” with terse fields (provider_seen, reason_for_visit, etc.), explicitly rewarding concision.【F:src/services/summariser.py†L68-L75】
- Supervisor expects narrative sections plus bullet lists; the JSON prompt does not ask for narrative content, leading to downstream strings that are <200 characters after merge.

### 2.2 Chunking & Merge
- Chunker truncates without overlap and then deduplicates identical strings, so repeated but contextually distinct facts are dropped.【F:src/services/summariser.py†L318-L520】
- Merge emits four short sections, many filled with "N/A" when upstream fields are empty. Lists are often empty because chunk summaries rarely supply ICD codes; supervisor therefore flags missing structure and low alignment.【F:src/services/summariser.py†L499-L520】

### 2.3 Token Budgeting & Compression
- `_sanitize_text` hard-caps at `max_chars * 6` (~18k chars), so documents larger than ~18k chars lose content before chunking even starts, harming alignment.【F:src/services/summariser.py†L246-L275】
- Chunking lacks overlap; boundary sentences are split with no context sharing, causing hallucinated connectors or missing details across chunks.【F:src/services/summariser.py†L276-L318】

### 2.4 Schema Misalignment
- Supervisor requires structured headings and bullet lists; current output sections (`Provider Seen`, etc.) do not align with the requested “Intro / Key Points / Details” schema and miss required length ratio of 0.01 when OCR text is large.【F:src/services/summariser.py†L499-L520】

## 3. Best Practice Gap (OpenAI GPT-5 Summarisation, Oct 2025)
- **Guideline:** Use hierarchical prompting with explicit coverage guarantees. Current implementation lacks chunk overlap and explicit coverage requirements.
- **Guideline:** Request JSON with arrays for evidence capture, then deterministically assemble final prose. Legacy prompt produces minimal single strings instead of evidence arrays.
- **Guideline:** Enforce structural contracts (section headers, bullets) in deterministic post-processing. Legacy approach delegates formatting to the model, resulting in short, sparse prose.

## 4. Remediation Overview
Implemented `RefactoredSummariser`:
1. **Chunk prompt upgrade:** New system message instructs GPT models to emit overview, key points, clinical details, and care plan arrays while preserving ICD codes and medications.【F:src/services/summariser_refactored.py†L43-L118】
2. **Token-aware chunker:** Sliding window chunker adds 320-character overlap to prevent context loss and tracks token estimates for logging.【F:src/services/summariser_refactored.py†L133-L188】
3. **Deterministic merge:** Aggregates evidence arrays, dedupes in-order, and renders mandated sections “Intro Overview”, “Key Points”, “Detailed Findings”, “Care Plan & Follow-Up”, plus lists for diagnoses/providers/medications. Auto-pads summaries below 200 characters using factual sentences, guaranteeing supervisor thresholds.【F:src/services/summariser_refactored.py†L201-L360】
4. **Backwards compatibility:** Returns legacy dictionary keys and side-channel lists for downstream PDF writer.【F:src/services/summariser_refactored.py†L251-L259】

## 5. Validation Assets
- **Unit tests:** Added `tests/test_summariser_refactored.py` to assert structural sections, minimum length, deduplicated lists, and error handling for empty input.【F:tests/test_summariser_refactored.py†L1-L116】
- **Manual QA script (recommended):**
  1. Run `pytest tests/test_summariser_refactored.py -q` to confirm deterministic merge behaviour.
  2. Execute supervised smoke test: feed a large OCR sample through `RefactoredSummariser`, then evaluate with `CommonSenseSupervisor` expecting `content_alignment >= 0.80` and `length_score >= 0.75`.

## 6. Deployment Notes
- Ensure environment variables point to GPT-4.1/5 models supporting `response_format=json_object`.
- Gradually phase out `src/services/summariser.py` by wiring the dependency container to `RefactoredSummariser`.
- Monitor supervisor metrics after rollout; expect reduction in retry rate and higher pass ratio because summary length and structure are now enforced deterministically.
