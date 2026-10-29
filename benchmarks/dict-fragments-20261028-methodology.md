# Dictionary-to-NDJSON benchmark methodology — 28 October 2026

This is an offline research artifact, not a production API or architecture change.
The independent harness is [dict_fragments_20261028.py](dict_fragments_20261028.py).
It does not import Assquack, PyArrow, Prefect, credentials or customer data.

## Published matrix

| Axis | Values |
| --- | --- |
| Narrow rows | 1,000; 10,000; 100,000 |
| Wide rows | 100; 1,000; 10,000 |
| Fragment targets | 65,536; 262,144; 1,048,576 bytes |
| Processing batch sizes | 128; 2,048 rows for bounded paths |
| Dictionary lists | Complete shape × size × target × path × applicable batch matrix |
| Streaming | Both medium shapes, 262,144-byte target, all paths and applicable batches |
| Repetitions | Three measured trials per configuration; 140 configurations |
| Warmup | Four discarded small trials, one per architecture |
| Order | Seeded shuffle independently for each repetition |
| DuckDB threads | One for both writing and reading |

The four paths are buffered jsonlines record writes; direct per-record Python
encoding accumulated into a bounded byte buffer; bounded NDJSON batch buffers
read by DuckDB and COPY'd individually; and whole-input JSON-array buffering,
DuckDB reading and COPY. Whole-input buffering is a comparison baseline, not a
DuckDB requirement. There is no dictionary-to-Arrow conversion.

## Measurement contract

Python 3.13.9, DuckDB 1.5.2, jsonlines 4.0.0, attrs 26.1.0, psutil 7.2.2 on
Windows 11; hardware and full configuration are recorded in run metadata.
The interpreter is the existing Poetry environment. Additional dependencies are
loaded from existing local diagnostic directories without changing them.

Every measured trial runs in a fresh worker. Imports, worker startup, list input
construction, garbage collection, correctness validation, file enumeration and
cleanup are outside writing time. For streaming, deterministic records are
created in bounded batches; their measured generation time is subtracted from
wall time. The unadjusted wall time and subtraction are also recorded. Batch
slicing, iteration, encoding, buffer allocation, output directory creation,
file opening, rotation and flush/close are included. DuckDB connection creation,
relation binding/inference, COPY and connection close are included for DuckDB
writers. Python writers do not create an unused DuckDB connection.

All Python serialization uses `json.dumps(ensure_ascii=False,
separators=(",", ":"), allow_nan=False)` and UTF-8. jsonlines uses that same
serializer returning bytes. Direct writing accumulates a bytearray, flushed
at 64 KiB or file rotation, with at most one additional encoded record. Both
Python paths use 64 KiB buffered binary file I/O and rotate after a complete
record crosses the target. Batch size controls dictionary collection separately
from this byte buffer and from the fragment target. Streaming currently constructs
at most a bounded number of dictionary batches; it does not materialize all rows
unless the whole-input baseline explicitly requires it.

DuckDB batch input is NDJSON in StringIO; whole-input input is a JSON array in
StringIO. Both use full sampling and disable map inference. DuckDB necessarily
re-encodes JSON, may widen numeric types, and fills absent fields with nulls.
Separate batch COPY calls deliberately use separate output subdirectories;
small final files are retained in the measurements. FILE_SIZE_BYTES is a target,
not a strict byte cap. NDJSON files have `.json` suffixes in this harness.

Writes use filesystem caching, without fsync. The subsequent read is warm-cache,
particularly because correctness validation reads the fragments first. No cold
cache or power-loss durability result is claimed. Warmups prime operating-system
and library-file caches; each measured worker still starts fresh, so per-process
DuckDB setup remains timed. Trials are sequential, but the host is not reserved
and background activity is uncontrolled. Three trials support comparisons and
ranges, not strong statistical significance for tiny differences.

RSS is sampled at a requested 1 ms interval, including Python and native DuckDB
allocations. The GIL and OS scheduling can miss short-lived peaks; these are
observed peaks, not exact allocation maxima. Each result records imported-process
RSS, baseline after input construction, and absolute writing peak. Baseline
subtraction does not erase input-list memory: report absolute and incremental
memory together. Streaming generation is excluded from timing but included in
memory observations. The reading phase has its own baseline and peak; allocator
retention and prior validation can affect it. List source memory is released
before reading, though allocators may retain pages.

Combined-fragment reading uses `union_by_name=True`, `sample_size=-1`,
`field_appearance_threshold=0.0`, `map_inference_threshold=-1`, and
`hive_partitioning=False`. Inference time includes reader connection creation and
relation binding/schema discovery. Read time includes that inference plus full
materialization into an in-memory DuckDB table. It excludes validation fetches and
reader connection close. Combined time is writing plus this read/materialization
time; it excludes the validation between them. This deliberately isolates native
DuckDB reading; it is not a measurement of the complete Assquack staging pipeline.

## Correctness gate

All four paths first pass a separate 260-record semantic probe containing a
heterogeneous field (integer, string, object and null), numeric widening, nested
values, Unicode and late fields on both sides of a batch boundary. Timed fixtures
contain late top-level and nested keys, nulls, booleans, exact quarter-valued
numbers, arrays, escaping and Unicode. Wide fixtures add twelve sizeable strings.

Every timed output is decoded and every record/key/value compared to deterministic
source records, with IDs checked for duplicates and omissions. The same check is
repeated after full combined-fragment inference and table materialization, using
DuckDB to_json to decode native nested/JSON fields correctly. Added null keys are
permitted; missing keys and non-null value changes fail the trial. Exact numeric
widening is permitted, but bool/numeric coercion is not. The normalization counter
counts rows with added null keys; Python equality does not count integer-to-float
widening. Probe schemas record DOUBLE for mixed integer/float numbers and JSON for
heterogeneous values. These are explicit semantic conversions, not field loss.
This scope is JSON-compatible synthetic data, not arbitrary Python objects or a
production schema-enforcement policy.

## Reproduction

From the repository root:

```powershell
poetry run python benchmarks/dict_fragments_20261028.py --run
poetry run python benchmarks/summarize_dict_fragments_20261028.py benchmarks/results/<run>.jsonl
```

Each run creates uniquely named metadata and raw JSONL results in `benchmarks/results/`
and uses only its own UUID subdirectory under ignored `.tmp-dict-fragments-20261028/`.
Validated timed fragments are removed after their size statistics are recorded;
small semantic-probe fragments remain in that run's scratch directory. Existing
benchmark artifacts are never replaced. Summarization creates CSV and JSON tables
with median timings, timing ranges, RSS, file-size distributions and counts.

## Related evidence

- [Prior diagnostics](ndjson_diagnostics.py): inspected for measurement reuse; its
  Arrow dictionary route was not reused.
- [Schema inference](../docs/schema-inference.md): current Assquack inference policy.
- [Storage model](../docs/storage-model.md): fragments in the existing architecture.
