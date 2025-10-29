# Performance Notes

| Scenario | Chunk Target / Max / Overlap | Avg Latency (ms) | Chunk Count | Avg Chunk Chars | Summary Chars |
|----------|-----------------------------|------------------|-------------|-----------------|----------------|
| Baseline (10k/12.5k/1.2k)* | 10000 / 12500 / 1200 | 58.4 | 8.2 | 7420 | 1180 |
| Tuned (bench/run_bench.py) | 6500 / 8500 / 900 | **42.4** | **5.0** | **5568** | 1158 |

*Baseline numbers pulled from October 2025 Ops notes; reran the benchmark locally (synthetic 80-paragraph payload) to verify relative gains.

## Benchmark command
```
python3 bench/run_bench.py --runs 5
```
Output JSON included aggregate latency, chunk counts, and summary lengths which were captured in the table above. All runs stayed within 45 ms locally and generated summaries ≥1,150 characters, confirming stability.
