# Empty-probe benchmark — 3 November 2026

A = `limit(1).fetchone()` then COPY only if nonempty. B = direct COPY, deleting zero-record output. Both use identical fragment counting, registration and completion markers.

**Neither wins universally.** A saves 21-57% on all five 10k-row empty workloads. B wins the million-row empty scan (6.63 vs 13.11 ms) and empty selective filter (74.49 vs 228.78 ms): 49% and 67% less elapsed time respectively. For nonempty grouped aggregation and window queries, A costs 39-99% more in the initial matrix; bounded reruns confirm B's advantage. Nonempty scans/filters/joins generally have overlapping ranges, so their smaller median differences do not establish a winner.

Median total capture milliseconds (three measured repeats). Delta = (A/B - 1) * 100: positive favours B; negative favours A. Probe is part of A's total. An asterisk marks overlapping observed A/B ranges; it is not a significance test. Ranges are in summary.json.

| Query | Input | Output | A ms | B ms | Delta A vs B | A probe ms |
|---|---:|---:|---:|---:|---:|---:|
| scan | 10,000 | 0 | 1.17 | 2.75 | -57.5% | 0.79 |
| scan | 10,000 | 10,000 | 11.20 | 10.77 | +4.0%* | 0.77 |
| filter | 10,000 | 0 | 3.58 | 4.65 | -22.9% | 3.12 |
| filter | 10,000 | 31 | 9.02 | 6.75 | +33.6%* | 1.61 |
| join | 10,000 | 0 | 2.33 | 3.83 | -39.3% | 1.81 |
| join | 10,000 | 10,000 | 12.93 | 11.95 | +8.2%* | 1.00 |
| aggregate | 10,000 | 0 | 1.70 | 2.97 | -42.9% | 1.34 |
| aggregate | 10,000 | 1,000 | 7.63 | 5.16 | +47.7% | 2.00 |
| window | 10,000 | 0 | 7.91 | 10.06 | -21.4% | 7.25 |
| window | 10,000 | 1,000 | 16.25 | 9.58 | +69.6% | 6.12 |
| scan | 100,000 | 0 | 2.83 | 4.71 | -39.9% | 2.15 |
| scan | 100,000 | 100,000 | 69.56 | 62.68 | +11.0%* | 0.79 |
| filter | 100,000 | 0 | 25.49 | 25.03 | +1.8%* | 25.00 |
| filter | 100,000 | 364 | 31.47 | 32.52 | -3.2%* | 1.15 |
| join | 100,000 | 0 | 9.99 | 10.83 | -7.7% | 9.51 |
| join | 100,000 | 100,000 | 108.54 | 119.99 | -9.5%* | 1.71 |
| aggregate | 100,000 | 0 | 4.94 | 5.47 | -9.7%* | 4.46 |
| aggregate | 100,000 | 1,000 | 10.21 | 7.33 | +39.2% | 3.89 |
| window | 100,000 | 0 | 70.32 | 51.60 | +36.3%* | 69.73 |
| window | 100,000 | 10,000 | 123.18 | 61.92 | +98.9% | 55.14 |
| scan | 1,000,000 | 0 | 13.11 | 6.63 | +97.7% | 12.63 |
| scan | 1,000,000 | 1,000,000 | 340.42 | 342.09 | -0.5%* | 0.91 |
| filter | 1,000,000 | 0 | 228.78 | 74.49 | +207.1% | 228.16 |
| filter | 1,000,000 | 3,955 | 89.90 | 99.36 | -9.5%* | 1.41 |
| join | 1,000,000 | 0 | 29.77 | 39.46 | -24.5%* | 29.26 |
| join | 1,000,000 | 1,000,000 | 444.29 | 404.79 | +9.8%* | 3.56 |
| aggregate | 1,000,000 | 0 | 13.79 | 15.06 | -8.4%* | 13.22 |
| aggregate | 1,000,000 | 1,000 | 39.78 | 25.05 | +58.8%* | 18.74 |
| window | 1,000,000 | 0 | 389.53 | 393.94 | -1.1%* | 388.70 |
| window | 1,000,000 | 100,000 | 834.19 | 515.90 | +61.7% | 342.42 |

Five variable cases received one bounded rerun (one warmup, three measured pairs). Selection: either strategy's max/min > 1.4 and max-min > 10 ms. Original observations above are retained, not replaced. No sleep was observed; scheduling/filesystem effects are possible. The large absolute shifts show why fine percentage claims are unsafe.

| Repeated case | A ms | B ms | Delta A vs B |
|---|---:|---:|---:|
| scan-100000-nonempty | 60.70 | 59.20 | +2.5% |
| join-100000-nonempty | 74.50 | 76.15 | -2.2% |
| window-100000-empty | 36.50 | 42.90 | -14.9% |
| window-100000-nonempty | 82.71 | 47.69 | +73.4% |
| aggregate-1000000-nonempty | 21.37 | 11.44 | +86.7% |

The 100k empty window reverses its initial winner; treat it as unstable. The nonempty 100k join's apparent A advantage shrinks to about 2%, with overlapping ranges. Empty joins favour A by median at all sizes, but million-row ranges overlap. Empty aggregation favours A by median (43%, 10%, 8% less time), with overlap at 100k and 1m. The million-row empty window is effectively tied (1% median difference).

Plans confirm actual HASH_JOIN, HASH_GROUP_BY and WINDOW operators; empty cases are not optimized to EMPTY_RESULT. A's window probe replaces the final ORDER_BY with TOP_N, but still computes the upstream window across all input. Successful scan/filter probes can stop early; aggregate/window probes must do substantial upstream work before COPY does it again. For the million-row empty selective filter, EXPLAIN ANALYZE shows full-query wall/scan-operator times of 72/280 ms versus 229/230 ms with STREAMING_LIMIT. This is consistent with reduced parallelism in the probe, not an optimized-away empty predicate.

All 280 trials passed exact JSON multiset/row equivalence, fragment and marker checks, no residual views/Parquet, and relation reuse. B removed its zero-record files; A skipped them. A separate sequence gate proved 11 sequence calls for 10 rows under A versus 10 under B. Volatile sources can therefore change values or emptiness between executions. Timed fixtures were deterministic.

This is a warmed, local synthetic benchmark: DuckDB 1.5.2, Python 3.13.9, Windows 11, 8 logical CPUs, four DuckDB threads, 4 GB memory limit, 64 MB fragment target. Timings include probe/COPY/finalization and exclude fixture creation and correctness checks. Doer and reviewer confirmed their full validations had finished before timing; no heavy collaborator work overlapped. Three repeats cannot establish small differences or generalize to remote sources/cold disks. No production code or configuration was changed and no implementation was selected.

Verification: correctness gates, full matrix and bounded reruns; Ruff lint/format; isolated Pyright; whitespace check. See [method and reproduction](README.md), [raw trials](raw.jsonl), [reruns](reruns.jsonl), [plans](plans/), [environment](environment.json).
