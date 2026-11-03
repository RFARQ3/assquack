# Fragment capture benchmark, 2 November 2026

This is an offline benchmark, not an implementation of the phase-07 proposal.
One configured file-backed database is bound to DuckDB's actual module default
connection in every worker. No customer data, credentials or live services are used.

## Compared implementations

- Current: import the working-tree `NDJSONFragmentWriter` unchanged. Source-session
  JSON serialization writes temporary Parquet; a new connection writes NDJSON.
- Historical (primary baseline): obtain the complete writer class directly from
  Git revision `5ab0e2192422b3bce3df58fd860d5c6dd4ba5007` and execute its AST unchanged.
  Record its source SHA-256 in every result. Its persistent `fragment_source` view
  is preserved, including its read-only-transaction limitation.
- Direct: use that historical class, replacing only `relation.query(view, COPY)`
  with `duckdb.execute(COPY relation)`. The relation is a local variable in that
  method and belongs to the actual default connection. Counting, empty-output
  removal, registration, renaming and finish marker are unchanged.

No Python row-conversion writer, MAD replica, recombination or connection API
redesign is included. No production source or architecture documentation changes.

## Fixtures and execution

Each fresh worker creates a synthetic BIGINT ID table and a 1,000-row dimension
table in its own instance of the single configured database, outside capture.
It then constructs a lazy scan/projection or hash-join/projection relation.
String generation, arithmetic, nested structure construction, scan and join
evaluation happen during capture, not fixture construction. Both queries produce
identical values; dimension offsets are zero. Query SQL, relation types and plans
are retained with every result. These are warm-buffer synthetic-source scenarios,
not cold-disk scans or precomputed wide result tables.

| Shape | Row counts | Returned columns |
| --- | --- | --- |
| Flat | 100,000; 1,000,000; 10,000,000 | 8: ID, boolean, double, decimal, date, nullable integer, 32/128-character strings |
| Nested | 100,000; 1,000,000 | ID, boolean, struct and nullable/empty/four-element list of structs |
| Wide | 10,000; 100,000; 1,000,000 | 50 integer and 50 × 64-character string columns |

All performance cases use a 64,000,000-byte COPY target. Actual sizes can exceed
the approximate target. Correctness gates use 0, 1 and 1,000 rows, both query plans,
both measurement lenses, all three writers, and a 1,000-byte target. Additional
edge records cover escaped strings, Unicode, HUGEINT, DECIMAL(38,9), timezone,
interval and nesting. They are reported separately from the performance shapes.

Each cell has one discarded warm-up. The smallest tier has six measured runs;
the larger tiers have three to bound repeated multi-gigabyte output and validation.
Six-run cells cycle all writer permutations; three-run cells rotate each writer
through each position once. Workers run sequentially under
the repository's shared benchmark timing lock. Fresh processes prevent retained
allocator memory from a previous capture becoming the next capture's baseline.
Ordinary OS background activity remains possible. No deliberate OS cache flushes.

One current-writer wide scan speed trial took 996 seconds; it remains in the
reported range. A later current-writer wide join memory worker exceeded the
original 1,800-second process deadline. No matching benchmark processes or trial
directory remained on inspection, and no timing/memory result was recovered.
Its phase and cause are unknown; it is recorded as an unmeasured timeout, not
fabricated as a completed capture or a known data-validation failure.

The remaining cases resumed by skipping recorded names, including that failure.
Their whole-worker deadline is 300 seconds (setup, capture, validation and final
scratch cleanup combined). On timeout the runner terminates its still-running
owned Windows process tree and records failure; it stops if cleanup cannot be
confirmed. These deadlines censor slow cases, so successful-trial medians must be
read together with failure counts and the retained 996-second observation.

A subsequent requested 50-second orchestration wait resumed after 3,371 seconds,
and a flat current-writer memory warm-up was also lost to its controller deadline.
This independently demonstrates an environment pause or scheduling interruption;
it does not establish the cause of each earlier outlier. No matching benchmark
processes remained when checked. The final resume uses Windows' temporary
`SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` on the controller,
restored on exit, to prevent idle sleep while running. Manual suspension remains
possible. The runner also preserves a complete child result if it is recoverable
after a controller deadline. Neither lost result was retrospectively invented.

## Connection settings

DuckDB is pinned by an executable assertion to 1.5.2. Source connections use four
threads, an 8 GB engine memory limit, UTC and a local spill directory. All use the
same host and output drive. The memory limit is per engine, not a process cap.

The faithful profile leaves the current writer's extra connection at DuckDB
defaults. Its defaults are recorded explicitly; this profile is not a claim of
equal effective resources. The controlled profile wraps creation of that extra
connection to apply the same four-thread/8 GB/UTC configuration and an equivalent
spill directory. Configuration time is included in capture. This adaptation is
benchmark-only; neither the transport algorithm nor production files change.
The historical and direct paths need no extra capture connection. Source and
bridge effective settings are recorded. A disposable probe reads bridge settings
before handoff, never inside the timed interval.

The fixture database is opened directly, without Assquack metadata bootstrap,
because this isolates fragment writing. Its storage compatibility remains DuckDB's
recorded default `v0.10.2`, identically across all paths; production's
`connect_database` explicitly selects `v1.5.0`. This benchmark does not assess that
storage-format setting. On this host, faithful bridge defaults are eight threads,
25.0 GiB, Australia/Sydney and `.tmp`; configured source memory displays as 7.4 GiB
because the requested 8 GB is decimal.

## Measurement boundaries

Speed starts immediately before `write_relation(relation)` and ends after
`finish()` returns. It includes lazy evaluation, writes, connection creation and
closure inside the writer, Parquet deletion, counting NDJSON delimiters, removing
empty files, registering and renaming fragments, and `_COMPLETE` creation. It
excludes process startup, fixture creation, initial source connection and writer
construction, validation, subsequent source closure and scratch-directory removal.
This is writer capture, not the full pipeline's database reopening or staging.
Historical view lifetime is left faithful to the old implementation: it remains
until source connection closure outside capture. No fsync is added to native COPY;
completion is the implementations' existing API/file-close semantics.

Memory is measured in separate instrumented runs within this same first stage,
over the same capture boundary. The worker calls Windows GetProcessMemoryInfo
on its own process handle, recording working set (RSS) and PrivateUsage (private
commitment), in that order. Report baseline at handoff, sampled absolute peak and
peak-minus-baseline. Native DuckDB allocations are included; the venv launcher is
not sampled. A worker thread targets 5 ms sampling and adds an endpoint sample.
Actual sample count and largest gap are recorded. Short-lived peaks can be missed;
these are sampled peaks, not guaranteed maxima. Sampling thread setup precedes
handoff, and joining follows capture. Speed-only runs have no sampler or sleeps.
Memory runs include the sampler's own small Python allocations; they are not
subtracted from the process figures. Memory summary cells are the independent
medians of each baseline, peak and increment series.

## Correctness and retained evidence

After capture, inspect the directory against the registered fragment list. Empty
input must produce zero data fragments and only the `_COMPLETE` control marker.
Every registered fragment must have positive row count and byte size; no partial
or Parquet artifacts may remain. Counting and empty-file removal are inside both
measurement boundaries. No eager full-data count precedes capture.

Small gates parse every record with Python's exact Decimal JSON parser and compare
against the lazy query's `to_json` results after timing. Performance cases read
all files with explicit original column types. The smallest and middle tiers,
plus the first two largest-tier warm-ups, used bidirectional EXCEPT ALL against
the source query. This preserved duplicate multiplicity but generated substantial
validation spill files for million-row wide output.

The remaining largest-tier trials use an equivalent bounded check specialized to
these deterministic fixtures: recompute every expected field from each record's
ID, compare the complete typed record, and require exactly N records with N
distinct integer IDs spanning 0 through N-1. Both source plans return identical
values because the dimension offsets are zero. This checks all values and detects
missing, duplicate and out-of-range records without materializing two wide result
sets. Ten tests cover all shapes/plans and rejection of corrupted or incomplete
records. The change affects post-capture verification only; source SQL, query
plans, writer algorithms, settings and measurement boundaries remain unchanged.
Later raw records explicitly name `streaming_fields_and_ids` and include separate
validation duration; the first few records using it predate those diagnostic fields.

These typed comparisons avoid treating harmless numeric formatting differences as
data loss. Output fragment
sizes and row counts are saved per trial. Validation is excluded from speed and
memory windows. Failed validation is recorded as failure, never as a successful
performance result. Failed artifacts remain available; successful synthetic files
are removed after verification to bound disk use.

Early validator-development failures are retained in separately named JSONL files:
one used a nonexistent Python reader API; another compared JSON text directly;
another exposed precision loss in DuckDB's typed JSON reader for very large
decimals. These are not benchmark evidence. The final small-case validator uses
Python Decimal independently of that reader. The current bridge's decimal loss
is visible in the raw NDJSON; the historical and direct paths preserve the value.

## Reproduction

Run from the repository with its Poetry environment:

```powershell
poetry run python benchmarks/capture_20261102.py --gates
poetry run python benchmarks/capture_20261102.py --compatibility
poetry run python benchmarks/capture_20261102.py --tier 0
poetry run python benchmarks/capture_20261102.py --tier 1 --repetitions 3
poetry run python benchmarks/capture_20261102.py --tier 2 --repetitions 3
```

Output goes to `benchmarks/capture-results-20261102`. Existing result files are
never overwritten. `--resume` skips already recorded trial names; use a reviewed
new value for the script's `OUTPUT` directory for a fresh rerun.
The separate edge precision failure does not authorize changing production code.

## Related artifacts

- [Findings and limitations](capture-20261102-report.md)
- [Complete results table](capture-results-20261102/results.md)
- [Harness](capture_20261102.py)
- [Validation tests](test_capture_20261102.py)
