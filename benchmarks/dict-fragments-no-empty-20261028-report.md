# Dictionary-origin repeat with no empty completed fragments

The earlier 100,000-row narrow whole-input read slowdown did not reproduce: its median is 0.855s, compared with the previous 1.145s. Empty-file cleanup cannot be credited causally because no paired baseline artifacts containing empties remain. Other read results move in both directions.

Offline synthetic repeat, 28 October 2026. Write and DuckDB read/materialization durations are independent. Both narrow and wide records contain the same nested object and array; wide adds twelve text fields.

## Large datasets at a 262,144-byte fragment target

Three-trial medians, with observed minimum-maximum ranges. Batch choices match the previous report; they are not reselected for this run. Read includes connection, full inference and in-memory table materialization. No write/read sum is presented.

| Dataset rows | Writer | Batch rows | Actual bytes | Write seconds (range) | Previous write seconds | Read seconds (range) | Previous read seconds |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 100,000 narrow, same nesting | jsonlines | 128 | 19,219,169 | 0.531 (0.491-0.600) | 0.496 | 0.640 (0.626-0.768) | 0.760 |
| 100,000 narrow, same nesting | direct | 128 | 19,219,169 | 0.570 (0.451-0.736) | 0.494 | 0.683 (0.615-0.694) | 0.708 |
| 100,000 narrow, same nesting | duck_batch | 2048 | 22,430,961 | 3.475 (3.053-3.761) | 3.588 | 0.848 (0.676-0.896) | 0.785 |
| 100,000 narrow, same nesting | duck_all | whole | 24,104,177 | 1.629 (1.594-1.998) | 1.946 | 0.855 (0.841-0.857) | 1.145 |
| 10,000 wide, same nesting | jsonlines | 2048 | 47,488,882 | 0.287 (0.254-0.316) | 0.284 | 0.544 (0.471-0.598) | 0.557 |
| 10,000 wide, same nesting | direct | 2048 | 47,488,882 | 0.287 (0.287-0.307) | 0.258 | 0.514 (0.474-0.579) | 0.807 |
| 10,000 wide, same nesting | duck_batch | 2048 | 47,821,738 | 2.663 (2.410-2.664) | 2.053 | 0.479 (0.458-0.549) | 0.506 |
| 10,000 wide, same nesting | duck_all | whole | 47,977,386 | 1.536 (1.401-2.015) | 1.441 | 0.558 (0.467-0.668) | 0.440 |

## Completed file layout and null padding

All completed fragments and all inference inputs are nonempty. COPY transient empty files were removed after close, inside write timing. Sizes below include every retained file; undersized nonempty files remain.

| Dataset rows | Writer | Retained files | Removed transient empties | Bytes per file min / median / max | Explicit null values | Added null keys | Added null bytes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 100,000 narrow | jsonlines | 74 | 0 | 73,081 / 262,266.0 / 262,330 | 114,286 | 0 | 0 |
| 100,000 narrow | direct | 74 | 0 | 73,081 / 262,266.0 / 262,330 | 114,286 | 0 | 0 |
| 100,000 narrow | duck_batch | 49 | 49 | 412,004 / 456,555 / 497,516 | 114,286 | 200,374 | 3,211,792 |
| 100,000 narrow | duck_all | 49 | 1 | 412,004 / 495,467 / 497,516 | 114,286 | 288,438 | 4,885,008 |
| 10,000 wide | jsonlines | 179 | 0 | 233,673 / 265,927 / 266,248 | 11,429 | 0 | 0 |
| 10,000 wide | direct | 179 | 0 | 233,673 / 265,927 / 266,248 | 11,429 | 0 | 0 |
| 10,000 wide | duck_batch | 5 | 5 | 8,678,499 / 9,789,648 / 9,791,600 | 11,429 | 20,652 | 332,856 |
| 10,000 wide | duck_all | 6 | 1 | 4,233,650 / 9,818,931.5 / 9,830,512 | 11,429 | 28,844 | 488,504 |

Added null bytes count the UTF-8 key, colon, null and one separator for each added key. These fixtures already have other members at every padded object level, so this is the exact padding-byte contribution. Explicit nulls include array elements and optional fields. Other encoding differences are not attributed to padding.

## Measurement and correctness

420 measured trials cover 140 configurations, with three fresh-worker repetitions and four discarded warmups. Every one of 7,923,300 records was validated both in fragments and after DuckDB materialization. Four heterogeneous semantic probes passed. Retained empty files: zero; measured transient empty files removed: 999.

The prior size matrix, targets (65,536 / 262,144 / 1,048,576 bytes), batch sizes (128 / 2,048), source modes and stdlib JSON serializer are unchanged. The order uses independently shuffled repetitions with seeds 9100-9102. Every worker holds an OS byte-range exclusive lock at `.tmp-fragment-benchmark-timing.lock` throughout its workload, including reads; the other diagnostic uses the same lock. Waiting is outside timing. OS process exit releases the lock.

Write timing includes dictionary-to-JSON conversion, native parsing/COPY where applicable, writes, flush/close, output enumeration and empty-file cleanup. Input-list construction and validation are excluded. Stream generation is subtracted, with wall time and subtraction retained in raw results. There is no fsync, matching the baseline. Python writers open files only for actual records. COPY may create transient empty files; the harness does not register or read them.

Every reader uses DuckDB 1.5.2, one thread, `sample_size=-1`, `union_by_name=True`, `field_appearance_threshold=0.0`, `map_inference_threshold=-1`, `hive_partitioning=False`, newline-delimited JSON and the same full table materialization. Correctness permits only absent-to-null padding and exactly equal numeric widening, while requiring explicit nulls, all late keys, nested values, booleans and record identity. Schemas are retained in every raw result. No null semantics were changed to improve speed.

Reads follow fragment validation and null profiling and therefore use warm filesystem caches. The extra profiling pass is outside timing but can affect cache/allocator state. No cold-cache, fsync, network filesystem or power-loss durability claim is made. RSS sampling is approximate. Background activity is uncontrolled despite serialization with the other benchmark. Three trials provide ranges, not statistical proof.

The baseline timed fragments were removed by its harness; retained scratch artifacts contain no empty files. Consequently no paired old-artifact empty-file control was available, and empty-file causality is not established. Differences between independent old and new runs cannot isolate cleanup cost or empty-file overhead. Writer identity does not itself control reading speed; bytes, schema, row/file layout, parsing and host conditions matter.

## Reproduce and inspect

```powershell
poetry run python benchmarks/dict_fragments_no_empty_20261028.py --run
poetry run python benchmarks/summarize_dict_fragments_no_empty_20261028.py benchmarks/results/<run>.jsonl
```

- [Raw results](results/dict-fragments-no-empty-20261028-d24f8a4ee6.jsonl)
- [Metadata](results/dict-fragments-no-empty-20261028-d24f8a4ee6.metadata.json)
- [All configuration summaries](results/dict-fragments-no-empty-20261028-d24f8a4ee6.summary.csv)
- [Validation](results/dict-fragments-no-empty-20261028-d24f8a4ee6.validation.json)
- [Harness](dict_fragments_no_empty_20261028.py)
- [Summarizer](summarize_dict_fragments_no_empty_20261028.py)
- [Previous report](dict-fragments-20261028-report.md)

## Observations and explanations

All repeat trials match the corresponding baseline total bytes, inferred schema and nonempty file-size distribution. This supports that the data and nonempty layout were preserved; it is not a paired timing control. The repeat also times output enumeration and empty-file cleanup for every path; baseline enumeration was outside write timing. This modest additional work must not be mistaken for an underlying writer change.

At 100,000 narrow rows / 262,144-byte target, whole COPY reads in 0.855s median, versus the old 1.145s; bounded COPY (2,048 rows) reads in 0.848s median, versus the old 0.785s. Consult the ranges above before ranking them.

Both COPY paths retain 49 files: 48 with 2,048 rows and one with 1,696 rows. Whole COPY adds 88,064 more absent-to-null keys than bounded COPY, all explained by globally padding across_late in early rows instead of only within batches where it appears. Those keys account for exactly 1,673,216 extra bytes. The final inferred schemas are equal. More bytes is a plausible read-cost factor, but these measurements do not isolate its contribution.

For wide rows the added text dominates output bytes, while COPY produces far fewer, larger files than Python rotation. Reduced file-opening and per-file inference work is a plausible explanation for some read differences; writer identity is not an explanation and this experiment does not isolate file-count effects.

The separate write results include JSON serialization, native parsing and re-encoding in the COPY paths. Bounded COPY repeats binding/COPY for each batch. These are real differences in work performed, not evidence that the framework is broken.

## Checks

The harness and summarizer passed Ruff lint/format and scoped Pyright checks. The summarizer asserts full matrix/repetition coverage, source hash, record totals, positive file sizes, byte totals and baseline layout/schema equivalence. git diff --check passed. Production tests were not run: no package code, public contract or production behavior changed.
