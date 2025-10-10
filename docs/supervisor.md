# Common Sense Supervisor (v11k)

The **Common Sense Supervisor** guards medical summaries before they are
delivered to Google Drive or rendered as PDFs. It supplements the existing
summarisation pipeline with deterministic heuristics that verify the
structure, length, and topical alignment of generated content.

## Responsibilities

1. **Document pre-scan**
   * Collect page count, OCR text length, and approximate file size.
   * Flag sources with more than 50 pages or 100k characters as
     *multi-pass* candidates. These use higher minimum length thresholds
     (≥600 characters vs. the baseline 200 characters).
2. **Post-summary validation**
   * Predict a target length of at least 1% of the OCR text length.
   * Require `length_score ≥ 0.75`, `content_alignment ≥ 0.80`, and
     structural checks (≥3 recognised headers, ≥1 bullet/numbered list).
   * Large documents (≥100 pages) must contain ≥3 paragraphs or exceed
     1,000 characters.
   * Compute alignment using stop-word filtered uni/bi-gram overlap
     against the OCR text (no external NLP dependencies).
3. **Retry and merge orchestration**
   * Attempt up to three retries with tighter chunk sizes to force
     diversity when the initial summary fails validation.
   * Score candidates via `0.4 * length_score + 0.6 * content_alignment`
     and pick the best-performing version.
4. **Structured logging**
   * Emit `supervisor_validation_started`, `supervisor_flagged`,
     `supervisor_retry`, and `supervisor_passed` events with doc stats
     and scoring metadata.

## Integration points

* `src/main.py` wires the supervisor into `/process` and `/process_drive`.
  Drive uploads are blocked unless the supervisor passes.
* Responses include a `validation` object with the full scoring payload
  (for `/process_drive`) and HTTP headers for `/process` streams.
* The supervisor is stateless and safe for reuse across requests.

## Testing strategy

* `tests/test_supervisor.py` simulates a 250-page OCR document and
  verifies multi-pass detection, retries, and alignment scoring.
* Existing endpoint tests now assert that the supervisor-approved
  validation payload is present for Drive responses.

## Tuning knobs

* `baseline_min_chars` – default 200 characters for short documents.
* `multi_pass_min_chars` – default 600 characters when multi-pass is
  required (pages > 50 or OCR text length > 100k).
* `max_retries` – defaults to 3; can be lowered via dependency injection
  if environments require stricter latency budgets.

These thresholds were selected to balance enforceable structure with the
existing summariser outputs while remaining deterministic for unit tests.
