# Independent empty-probe benchmark

This directory is self-contained and imports DuckDB, never Assquack. It compares
exactly two capture strategies: A probes `relation.limit(1).fetchone()`, skipping
COPY when empty; B executes COPY directly. Both use the same post-COPY newline
counting, zero-file deletion, fragment renaming/registration and `_COMPLETE`
marker. No production implementation decision is made here.

The pinned `capture()` routine adapts the direct path selected in
[`capture_20261102.py`](../capture_20261102.py): module-level `duckdb.execute`
resolves the local Python `relation` through a replacement scan. COPY options,
1 MiB counting buffers and fragment naming follow the historical writer at
`5ab0e2192422b3bce3df58fd860d5c6dd4ba5007`. No transient registration/view,
intermediate Parquet or eager full-result fetch occurs in capture.
`environment.json` records SHA-256 hashes of the actual harness and reference.

Run `run.py --gates-only`, then `run.py`, `rerun.py`, and `summarize.py` using the existing
DuckDB 1.5.2 Python environment. Do not run alongside heavy validation or another
benchmark. The runner refuses to overwrite `raw.jsonl`. Synthetic disposable
files are confined to `scratch/`; checked output files are removed after each
trial. The same absolute capture path is reused by both strategies.

The bounded matrix is five families, three input sizes (10k, 100k, 1m), empty and
nonempty output, two strategies, one warmup and three measured repeats: 240
trials, 180 measured. Four DuckDB threads, 4 GB memory, 64,000,000-byte fragment
target, one sequential timing worker. Alternating pair order is balanced across
the matrix (45 measured pairs start with each strategy). Fixtures are held in an
in-memory DuckDB database; JSON output uses the workspace disk. This is a warmed
local workload, not a cold-storage benchmark. Windows idle sleep is inhibited.

Timing starts after a fresh unevaluated relation is handed to `capture`, and
includes directory creation, the optional probe, COPY, record counting, cleanup,
registration and completion marker. Connection/fixture creation, SQL binding,
plans, correctness checks and disposal of completed output are outside timing.
Raw data includes wall time, process CPU time and separate probe time.

Every trial checks exact JSON multiset equality in both directions using
`EXCEPT ALL`, including duplicate counts and nested values. It verifies row
counts, only positive-count completed fragments, no leftover partials, completion
marker, unchanged view catalog, absence of Parquet and continued relation reuse.
Empty results must have zero completed fragments. The separate sequence gate
demonstrates a second source evaluation after a successful probe: A calls
`nextval` 11 times for 10 captured rows, B calls it 10 times. A probe does not cache
the relation. With volatile sources it can consume values, disagree with COPY
about emptiness, or change output; the timed fixtures contain no volatile inputs.

`plans/` retains SQL, logical source/output counts, optimized full/probe plans and
full EXPLAIN ANALYZE execution evidence. Simple nonempty scan is an unfiltered
projection. Its empty counterpart must add a substring predicate, so it measures
a real scan rather than an inherently empty table. The selective filter computes
MD5 and tests a suffix; `zz` never occurs in MD5 but the optimizer cannot infer
that. The join empty predicate uses values from both sides. Grouped aggregation
uses HAVING on a positive sum. Window queries order all input for row_number,
then retain no rows or the first 10%; they include final ORDER BY. LIMIT may
short-circuit scans or replace final sorting with TOP_N; saved plans disclose
these optimizations. Both strategies receive exactly the same SQL for a case.

Three repeats give descriptive timings, not statistical proof. Small differences
and overlapping ranges should be treated as noise. Fixture preparation and
post-run validation warm caches; external machine load and filesystem caching
remain limitations. Inspect raw repeats for pauses/outliers before interpreting.

Results: [table](results.md), [raw timings](raw.jsonl),
[ranges and medians](summary.json), [environment](environment.json),
[execution gate](gates.json).
