# Fragment capture comparison, 2 November 2026

Direct default-connection COPY did not establish a general speed or memory
advantage over the researched historical view-based COPY implementation. Both
native paths generally used much less time and memory than the current Parquet
bridge. Beating that bridge alone does not establish a reason to replace the
historical process.

Across the 16 controlled-settings scenario/query combinations, direct COPY's
median times ranged from 17.8% faster to 3.6% slower than historical COPY. The
median relative difference across those combinations was 0.4% faster. These are
descriptive comparisons, not a statistical significance claim; see the timing
ranges and environment interruptions below.

## Representative results

These are lazy scan/projection cases, with four threads and an 8 GB engine limit
on every participating connection. Each cell has three measured speed trials and
three separate memory trials. Source generation and output verification are
outside capture; the returned relation's evaluation remains inside it.

| Scenario | Current bridge, seconds | Historical baseline, seconds | Direct, seconds |
| --- | ---: | ---: | ---: |
| Flat, 10,000,000 rows | 16.42 | 6.73 | 6.69 |
| Nested, 1,000,000 rows | 1.63 | 0.75 | 0.76 |
| Wide, 1,000,000 rows × 100 columns | 38.07 | 13.22 | 13.28 |

Median sampled peak memory below is **RSS / private commitment**, in MiB.
Windows private commitment is distinct from resident working set.

| Scenario | Current bridge | Historical baseline | Direct |
| --- | ---: | ---: | ---: |
| Flat, 10,000,000 rows | 1,419.6 / 1,761.6 | 81.6 / 84.5 | 81.4 / 81.5 |
| Nested, 1,000,000 rows | 484.1 / 689.7 | 78.1 / 65.1 | 77.7 / 64.6 |
| Wide, 1,000,000 rows | 5,555.7 / 8,179.5 | 355.3 / 510.3 | 349.5 / 508.8 |

The [complete results table](capture-results-20261102/results.md) includes both
query plans and both settings profiles, timing ranges, actual fragment counts and
sizes, and baseline / peak / increment for **both** memory measures. Individual
fragment sizes, row counts, source types, SQL, plans, settings and worker PIDs
remain in the three `tier-*.jsonl` files beside it.

## Coverage and correctness

- All 984 scheduled performance records were attempted: **982 validated results
  and two unmeasured worker timeouts**. This includes 192 scheduled warm-ups.
  There are 791 validated measured trials: 396 speed and 395 memory trials.
- All eight proposed size/shape scenarios ran through all three writers, both
  lazy query plans and both faithful/controlled settings profiles. Smallest-tier
  cells have six repetitions per lens; larger cells have three attempted
  repetitions. One current-writer memory cell has two valid repetitions because
  the third timed out. The other lost record was a warm-up.
- The 144 small correctness gates produced **136 passes and eight failures**.
  All eight failures were the current bridge's high-precision decimal case.
  Historical and direct preserved the tested value. Current changed
  `12345678901234567890.123456789` into `12345678901234567000.0` in the NDJSON.
  This is a fragment serialization finding, not an end-to-end recombination claim.
- **All 48 empty-input checks passed:** zero data fragments and only the
  `_COMPLETE` control marker. Registered fragments were checked against files on
  disk; every completed data fragment had positive row count and size. Partial
  and Parquet artifacts were absent after successful capture. No eager source
  count was introduced to prevent empties.
- Separate read-only-transaction checks passed for current and direct COPY.
  Historical COPY failed when it attempted to create `fragment_source`, as
  expected from its actual implementation. Normal performance cases used a
  writable source connection so all three could complete.

## Interpretation limits

One faithful current-writer wide scan speed trial took **996 seconds**; the other
two took roughly 35 seconds. It remains in the range and was not silently removed.
Two worker results were lost to controller deadlines and are recorded without
invented capture times or memory figures. A separate requested 50-second wait
resumed 3,371 seconds later, demonstrating an environment pause or scheduling
interruption. The cause of each earlier anomaly is not established. Final resumed
runs used temporary Windows idle-sleep prevention, released when the controller
exited. Treat successful-trial medians alongside the failures and full ranges.

Memory sampling targeted 5 ms; the largest observed gap was **108.9 ms**. Peaks
include native allocations in the actual worker, not its venv launcher, but short
transient peaks can be missed. Memory runs include sampler overhead. Speed runs
have no sampler or artificial waits.

The current writer's faithful extra connection used eight threads and a 25 GiB
limit on this host. Controlled results explicitly adapted that connection to
four threads / 8 GB. Limits are per engine, not an aggregate process cap.
The fixture database retained DuckDB's default storage compatibility; production
bootstrap was outside this writer-only benchmark. The detailed
[methodology](capture-20261102-methodology.md) documents that boundary and the
post-capture validation change needed to avoid large verification spill files.

## Provenance and checks

- DuckDB **1.5.2**, Python **3.13.9**, Windows 11; Intel Core i7-11370H, eight
  logical CPUs, about 31.8 GiB usable physical RAM; local output on drive E:.
- Historical baseline: exact writer class from
  `5ab0e2192422b3bce3df58fd860d5c6dd4ba5007`, verified as the parent of
  `e564898e9dc2a3d904fc6e1745010418dc189cc1`. Its shared fragment model and SQL
  quoting helpers are unchanged relative to current HEAD.
- Current HEAD at inspection: `c477e7278f3f5a2c5a6d07e36e900ef1d83047d8`.
  The current writer file's SHA-256 matched before and after execution:
  `4465e769f556e809072c3f4cb136d89fe46a42e3d0aeab520e4b05fb68ce95b5`.
- Full-matrix audit passed: expected trial coverage, identical source SQL/types/
  plans within each scenario across paths, configured settings, fragment counts
  and recorded memory arithmetic. No result was substituted for a failed worker.
- Ruff lint and formatting checks passed. Harness/report-generator Pyright:
  zero errors. `git diff --check` passed.
- Focused repository fragment tests: **39 passed, eight skipped** because optional
  pandas/Arrow packages were absent. Bounded-validator tests: **10 passed**,
  including changed-value, missing-ID, duplicate-ID and shifted-ID rejection.

Only benchmark code, reports and generated evidence were added. Production code,
architecture docs, the unverified phase-07 proposal and unrelated user edits were
left alone. No commits were made. Recombination and downstream connection
ergonomics remain outside this stage.
