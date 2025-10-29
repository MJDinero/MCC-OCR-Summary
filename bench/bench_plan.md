# Benchmark Plan

## Objectives
- Validate that the tuned summariser chunk sizes (`target=6500`, `max=8500`, `overlap=900`) reduce latency and keep chunk counts manageable for 250–400 page PDFs.
- Record end-to-end summariser latency using the heuristic backend for deterministic local runs.
- Track average chunk size, chunk count, and summary length to ensure the guardrails maintain structure.

## Method
1. Use `bench/run_bench.py` with the bundled synthetic clinical payload or a large OCR-exported text file.
2. Run at least 5 iterations per configuration to smooth variance: `python bench/run_bench.py --input data/ocr_large.txt --runs 5`.
3. Capture JSON output and commit highlights into `PERF.md`.
4. Re-run the benchmark whenever chunk parameters change or before major releases.

## Metrics
- Average / p95 summariser latency (ms)
- Average chunk count per document
- Average chunk length (characters)
- Final summary length

## Acceptance
- Latency improves by ≥15% versus the previous 10k/12.5k chunk settings on a 250-page sample.
- Chunk counts stay under 40 for 250-page PDFs, preventing runaway LLM calls.
- Summary length remains ≥ target minimum, confirming the guardrail does not over-trim.
