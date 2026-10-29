# NDJSON writing and fragment sizing diagnostics

Research date: **28 October 2026**, Australia/Sydney. Synthetic local diagnostics
only. No production implementation, normalization contract, dependencies, or
architecture was changed. NDJSON remains the fixed inference input format.

## Recommendation and measured outcome

**Prefer native COPY for already typed Arrow/relation inputs if approximate
rollover is acceptable; retain a direct record encoder for dictionaries. Use
10–50 MB as the promising range, not 1 MB as a universal default.** This is a
diagnostic recommendation, not approval to relax the current per-record rule or
implement normalization routing. The 10 and 50 MB results often overlap; three
trials do not establish a reliable universal optimum between them.

The lowest combined median among measured configurations for each large input
is below. Each has three measured trials after warmup. W/R/T are independent
medians in seconds, so rounded W + R need not exactly equal T.

| Starting representation | Shape | Writer / sizing | Target MB | Write | Read + infer + candidate | Combined | Combined range |
|---|---|---|---:|---:|---:|---:|---|
| Arrow Table | Flat | COPY / native | 10 | 0.628 | 2.676 | 3.302 | 3.182–3.472 |
| Arrow Table | Nested | COPY / native | 50 | 0.633 | 0.903 | 1.579 | 1.516–1.656 |
| Dictionaries | Flat | orjson / batch | 50 | 0.413 | 2.481 | 2.894 | 2.729–2.994 |
| Dictionaries | Nested | orjson / batch | 50 | 0.572 | 0.955 | 1.527 | 1.438–1.760 |
| DuckDB relation | Flat | COPY / native | 10 | 0.619 | 2.608 | 3.227 | 3.183–3.537 |
| DuckDB relation | Nested | COPY / native | 10 | 0.731 | 1.000 | 1.721 | 1.703–2.051 |

For the same large Arrow inputs at **10 MB**, standard JSON with per-record
rollover took **6.391 s flat / 2.178 s nested** combined. DuckDB JSON with
per-record rollover took **3.841 / 2.100 s**; orjson with per-record rollover
took **4.080 / 2.003 s**. COPY took **3.302 / 1.593 s**. Thus the flat writer-only
gain of about 6.5× becomes about **1.94× combined**; nested improves about
**1.37× combined**. Repeated inference materially limits end-to-end speedup.

If the current per-record completion rule must remain, exclude native and batch
rollover from the eligible set. The fastest measured per-record large Arrow
combinations were DuckDB JSON at 50 MB for flat data (**3.743 s combined**) and
orjson at 50 MB for nested data (**1.841 s**). There is no single best encoder
across both shapes. Large dictionary/relation per-record paths were not in the
staged cross-input matrix, so these numbers do not establish their winners.

Small inputs do not justify a universal COPY route. For 1,000 flat rows, combined
medians for the preselected batch/native candidates were approximately 22–42 ms,
with direct orjson and DuckDB JSON often ahead of COPY. For small nested data,
the best choice varied by representation. Full measurements, including the
standard encoder and fixed-row cases, are in the linked tables.

## Correctness and sizing tradeoffs

All **468 timed trials across 156 configurations** passed exhaustive raw-record,
typed-row and inferred-type checks. Every large flat run wrote **176,460,961
bytes**, and every large nested run wrote **173,449,674 bytes**. Within these
fixtures the different encoders produced equal byte totals and equal values.
Fields populated only in the final 10% of rows were inferred correctly. No
reader schema or schema history was supplied.

The separate [compatibility probes](../../benchmarks/results/ndjson-2026-10-28-compatibility.json)
expose boundaries that the homogeneous timing fixtures deliberately do not hide:

- **Do not route arbitrary dictionaries through the tested `Table.from_pylist`
  conversion.** A top-level field absent in the first dictionary and present in
  the second was dropped before DuckDB serialization/COPY. Direct JSON and
  orjson preserved it, and the production reader inferred it. Nested late keys
  were retained in this probe. Dictionary-to-DuckDB timings apply only to the
  uniform-column fixtures, not to a generally lossless normalization path.
- **orjson is conditional on an explicit scalar-value policy.** In the probe,
  standard JSON emitted `NaN`, while orjson emitted null. The performance
  fixtures contain finite floats only. Do not silently introduce that semantic
  change. Arbitrary precision integers, decimals, dates, binary values, mixed
  scalar types and custom encoders are not comprehensively qualified here.
- Batch rollover can overshoot by a whole batch, not just one record. Wide
  Arrow batches produced maximum files of **3.986 / 11.917 / 51.375 MB** at
  **1 / 10 / 50 MB** targets. Native COPY matched those boundaries for the
  chunked Arrow input, but relation/dictionary COPY reached
  **7.948 / 15.854 / 55.347 MB**. Native rollover depends on its input/execution
  batching; the Python fetch batch size does not control it.
- Native COPY sometimes emitted an **empty trailing file** at the 1 and 10 MB
  nested settings. File counts and distributions include it. All nonempty
  records remained complete and the production reader accepted the file set.
  This does not prove failure cleanup or atomic publication behavior.
- Large fixed-row settings were **5,423 / 54,233 / 271,164 rows** for flat data
  and **260 / 2,602 / 13,008 rows** for nested data. Flat maximum file sizes
  were **1.078 / 10.777 / 53.349 MB**. They offered no clear general timing
  advantage sufficient to favor row counts over the requested byte policy.

Sampled absolute RSS for large cases ranged from roughly **0.43 to 1.4 GB**,
depending on source representation, candidate and allocator state. The raw
results record write and read peaks separately; the tables show maximum process
and incremental phase peaks. Source dictionaries account for substantial memory.
Because workers reuse allocators across their serial case, these measurements
are diagnostic footprints, not clean evidence that one writer intrinsically
requires that much more memory than another.

## Scope and supported combinations

| Writer | Bytes after each record | Bytes after 1,024 rows | Native approximate bytes | Fixed rows |
|---|---|---|---|---|
| Python `json.dumps`, buffered binary output | Yes | Yes | N/A | Yes |
| `orjson`, buffered binary output | Yes | Yes | N/A | Yes |
| DuckDB `to_json`, streamed to Python output | Yes | Yes | N/A | Yes |
| Native DuckDB JSON `COPY` | N/A | N/A | Yes | N/A |

The installed DuckDB 1.5.2 accepts JSON `FILE_SIZE_BYTES`. Direct probes reject
`ROW_GROUP_SIZE` and `ROW_GROUPS_PER_FILE` for JSON. COPY exposes no tested exact
record/batch/row-count rollover control. This benchmark does not simulate it with
repeated LIMIT/OFFSET scans, partitioning columns, or a write-then-resplit pass.
Native approximate rollover is a valid candidate, with its size distribution
reported separately from the current per-record completion rule.

The large Arrow matrix covers every supported cell at all three settings.
Large dictionary and materialized DuckDB relation inputs compare the three
preselected fast paths (`orjson`/batch, DuckDB/batch, COPY/native) at all three
settings. The 1,000-row cases cover every supported cell at the 1 MB setting for
all three input representations and both shapes. These are deliberately distinct
coverage sets; the large Arrow ranking cannot be generalized to dictionaries.

## Measurement contract

- Byte targets are **1, 10 and 50 decimal MB**. The independent conversion/fetch
  batch size is **1,024 rows**. Byte checks happen after a complete record or
  bounded batch. Row limits are calibrated outside timing from the first 1,000
  fixture rows; they are counts, not byte ceilings. Exact counts appear in the
  timing tables and raw results.
- Flat input has 900,000 rows, six scalar columns, nulls, a late-populated integer,
  and a 100-character label suffix. Nested input has 45,000 rows, variable-width
  Unicode text with quotes/backslashes/newlines, nested structs, nullable lists,
  and empty lists. Small cases have 1,000 rows of the same deterministic shapes.
  They are synthetic in-memory sources, not streaming network fixtures.
- Every candidate starts from the designated ready dictionary list, Arrow Table,
  or materialized DuckDB relation. Fixture creation is excluded. Conversion from
  Arrow batches to dictionaries, relation tuples to dictionaries, or dictionaries
  to Arrow for DuckDB serialization is included in write timing. The Arrow Table
  contains RecordBatches, but standalone RecordBatch dispatch is not separately
  measured. A ready single-thread writer connection is supplied to all paths.
- Each configuration gets one discarded warmup and three measured trials, in
  rotated order, executed serially. Warmup is per configuration. Results are
  statistical observations, not deterministic performance guarantees.
  Candidates were serial and this chat started no competing benchmark workloads.
  This was not a dedicated idle host: unrelated checkout edits occurred during
  the session, and other host activity was not controlled. Treat close rankings
  accordingly. Finalists were not extended to five repetitions.
- Python output uses buffered binary files, flush, `fsync`, close and rename.
  Native COPY completes and closes its files, then each is opened and `fsync`ed
  and renamed. All paths create a completion marker. Neither path fsyncs the
  containing directory, matching the current writer. There is no second pass to
  split completed files. Native COPY's visibility/failure semantics are not
  proven equivalent to the production partial-file protocol by this timing test.
- Read timing starts with a **fresh in-memory DuckDB connection**, includes
  `CurrentRunSchemaStrategy.resolve()` and `create_shaped_staging()`, and ends
  when the typed candidate table exists. Both actual reader invocations run,
  including the repeated inference during candidate creation and production
  `try_cast` projections and metadata columns. Reader options are recorded in
  the environment artifact. Database publication and persistent database I/O are
  outside scope.
  DuckDB's internal physical scan count is not separately instrumented; this
  measures the actual two-call production path rather than assuming one scan.
- Files have just been written. **OS caches are warm/unspecified, not cold.** No
  system caches are flushed. Closing/opening a DuckDB connection resets its
  connection state; it does not create a cold storage benchmark.
- Process RSS sampled every 5 ms includes Python and native memory. Absolute and
  incremental write/read peaks are recorded. Short peaks can be missed. Sources
  and writer connection are already resident, and allocator reuse across serial
  trials can reduce incremental RSS; absolute RSS is important context. These
  are process footprints, not allocator-attributed memory costs.
- Outside timing, every NDJSON record must end in a newline and match its
  deterministic expected value, with every ID present exactly once. Every typed
  candidate row is compared again, in ID order, and inferred types are asserted.
  This catches value loss from permissive casts. File sizes, row counts, bytes,
  versions, timing and sampled memory are retained per measured trial.

## Artifacts and reproduction

- [Benchmark script](../../benchmarks/ndjson_diagnostics.py)
- [Raw measured trials](../../benchmarks/results/ndjson-2026-10-28.jsonl)
- [Environment and capability probes](../../benchmarks/results/ndjson-2026-10-28-environment.json)
- [Separate compatibility probes](../../benchmarks/results/ndjson-2026-10-28-compatibility.json)
- [Full timing tables](2026-10-28-ndjson-timing-tables.md)
- [Table generator](../../benchmarks/summarize_ndjson.py)

Use the repository's Poetry environment with DuckDB 1.5.2. Benchmark-only
PyArrow 26.0.0, psutil 7.2.2 and orjson 3.13.0 are loaded from the ignored
`.tmp-normalization-benchmark/deps` directory. Install them there if absent;
do not add them to the project dependency declaration. Generated output lives
under ignored `.tmp-ndjson-diagnostics` and is removed after each verified trial.

```powershell
uv --cache-dir .tmp-ndjson-diagnostics/uv-cache pip install --python (poetry env info --executable) --target .tmp-normalization-benchmark/deps pyarrow==26.0.0 psutil==7.2.2 orjson==3.13.0
poetry run python benchmarks/ndjson_diagnostics.py --probe
poetry run python benchmarks/ndjson_diagnostics.py --all --output .tmp-ndjson-diagnostics/reproduction.jsonl
poetry run python benchmarks/ndjson_diagnostics.py --compatibility
poetry run python benchmarks/summarize_ndjson.py
```

The complete-run command refuses to overwrite existing raw results; the command
above writes a fresh scratch result and preserves the checked-in evidence. Choose
a new output filename for subsequent reproductions. The table generator uses the
checked-in evidence. A single case can be reproduced with, for example,
`poetry run python benchmarks/ndjson_diagnostics.py --kind arrow --shape nested`.
Single-case commands append measured trials to the results file. A failed run
leaves its current output for inspection rather than silently deleting evidence.

The writer named `json` follows the current encoding and completion policy but
uses the benchmark's shared batch/split harness. It is not a timing of every
production writer method, error wrapper or metadata allocation. DuckDB JSON is
fetched in bounded Python batches; this does not establish constant memory use
inside DuckDB. The process RSS measurements include that engine's allocations.

## Validation

- 468 measured trials, 156 distinct configurations, exactly three measured
  repetitions each; raw row counts, byte totals and correctness flags audited.
- Post-format smoke runs exercised all ten supported writer/sizing cells on
  small Arrow nested, dictionary nested and relation flat inputs. These discarded
  runs were not appended to the measured evidence.
- 34 existing fragment/schema tests passed:
  `poetry run pytest tests/test_fragments.py tests/test_schema_inference.py tests/test_schema_replacement.py -q --basetemp E:/projects/assquack/.tmp-ndjson-diagnostics/pytest-final-offline --tb=short`.
  Windows sandbox temporary-directory access initially failed; the same offline
  suite passed outside that restriction, using a fresh benchmark-owned directory.
- Ruff lint and format checks passed for both benchmark scripts. Pyright reported
  zero errors/warnings with the project runtime and isolated scratch dependency
  directories on its search path.
- Documentation link checks and `git diff --check` passed. The required
  repository terminology scan found only the existing historical cache/locking
  mention and its two validation references, outside the new research pages.
- No production source, package dependency, branch, commit or live service was
  changed by this diagnostic work. Unrelated concurrent checkout edits were left
  alone. Benchmark-generated fragment outputs were removed after verification;
  isolated dependencies, test scratch and the run log remain ignored.

## Related docs

- [Documentation index](../README.md)
- [Scope discussion and preliminary benchmark](../journal/2026-09-08-mvp-scope.md)
- [Schema inference](../schema-inference.md)
- [Fragment writer](../../src/assquack/_materialization/fragments.py)
