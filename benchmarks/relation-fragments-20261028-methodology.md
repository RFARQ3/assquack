# Existing-relation fragment benchmark methodology

This offline diagnostic compares direct DuckDB COPY with fetching an existing
DuckDBPyRelation in bounded Python batches and writing those records through
jsonlines or a direct UTF-8 byte buffer. It does not measure dictionary-origin,
pandas, Arrow, or production Assquack performance.

Run `poetry run python benchmarks/relation_fragments_20261028.py --run`.
Use `--smoke` for a smaller correctness matrix. Each invocation creates unique
result and scratch paths and preserves its completed fragments. Summarize a full
run using `poetry run python benchmarks/summarize_relation_fragments_20261028.py
benchmarks/results/relation-fragments-20261028-RUNID.jsonl` on one command line.
Existing diagnostic dependency directories are reused without installation.

## Inputs and preparation

The preserved [dictionary baseline](dict_fragments_20261028.py) supplies identical
fixture generation and Python writer algorithms. Narrow datasets contain 1,000,
10,000 or 100,000 rows; wide datasets contain 100, 1,000 or 10,000 rows. Both
contain Unicode and escaping, integer IDs, exact quarter-valued floating-point
numbers, booleans, explicit nulls, nested structs and lists containing integers
and nulls. Late fields first occur at row 127 and in the last tenth of the input.
Wide adds twelve long Unicode strings. At 100 rows the row-127 field is absent
from the relation altogether; other sizes exercise that field.

Before timing, each fresh worker streams the fixture to a temporary file and
uses full-sampling DuckDB inference to materialize the same in-memory table.
All routes receive `con.table("prepared")`; no route pays initial construction
cost and no lazy JSON source remains behind the relation. Preparation uses the
same options as readback below. It fills absent top-level and nested struct keys
with nulls; counts of added null keys and normalized rows are recorded separately.
The temporary source is removed before writing. The complete typed table is
resident in DuckDB, but no complete Python record list is required.

The relation's actual values are fetched in bounded batches before timing to
build per-ID SHA-256 hashes. Hash storage is 32 bytes per record. Original fixtures
are also checked against those values; only added null keys and exact numeric
widening are permitted during preparation. This validation warms the relation.

## Timed routes

Targets are 65,536, 262,144 and 1,048,576 bytes. COPY registers a view of the
prepared relation and executes one native COPY with `FORMAT JSON`, `ARRAY false`
and `FILE_SIZE_BYTES`; view registration/removal is timed. Python routes execute
that relation and fetch 128 or 2,048 tuples at a time, convert each batch to
records, and call the baseline writer. Fetching, conversion, serialization,
directory creation, file opening, rotation, buffered writes and flush/close are
all included in write time. Neither path has a whole-input Python fetch.

Both Python routes use compact standard-library JSON with `ensure_ascii=False`,
`allow_nan=False`, UTF-8, and a 64 KiB buffered binary handle. jsonlines receives
the same serializer returning bytes. The direct route accumulates a bytearray
and flushes at 64 KiB or file rotation. Python rotates after a complete record
crosses the target. Native COPY has approximate rollover semantics rather than
that same record-level policy; this is a complete-route comparison, not an
isolated encoder test or a claim that the target is a strict maximum.

Every route enumerates and stats its output files inside write timing. Any empty
transient COPY files are deleted before write completion and before registration
as read inputs. Removal counts are reported. Nonempty files, including small
final fragments, are retained. No empty completed fragments are accepted.
Write includes close/flush but excludes forced `fsync`, matching the baseline.
The already-existing source connection is closed after write timing.

Read time is measured independently: create a new one-thread DuckDB connection,
read the complete nonempty file list as NDJSON, discover schema, and fully
materialize an in-memory candidate table. All routes use `sample_size=-1`,
`union_by_name=True`, `field_appearance_threshold=0.0`,
`map_inference_threshold=-1` and `hive_partitioning=False`. Setup/inference is a
subset of read time, never an extra duration to add to read time. Read excludes
validation fetches and reader close. Write and read are never summed in reports.

## Correctness and scheduling

Every raw fragment record and every typed readback record must match its prepared
relation hash, including exact key sets, nulls, nested values and scalar JSON
types. IDs must be unique and complete. Reader schema must equal source schema.
No additional writer normalization is permitted. Validation is outside timing;
fragment validation occurs before read timing and warms filesystem caches.

There are 90 configurations, each with three fresh-process repetitions in a
seeded shuffled order, plus three discarded small warmups. DuckDB uses one thread
for all preparation, writing and reading. Metadata records dependency versions,
OS, CPU, RAM, interpreter, input configuration and source hashes.

Every worker takes the shared Windows OS byte-range lock on byte zero of
`E:/projects/assquack/.tmp-fragment-benchmark-timing.lock`. It holds the lock
through preparation, writing, reading and validation, so no worker in this
harness runs outside the lock. The other diagnostic uses the same lock.
Process exit releases it automatically. Waiting is excluded from timings.

Three trials show ranges, not statistical confidence. Background host activity
is uncontrolled; OS caches are warm, and no cold-cache, network filesystem,
fsync/durability or peak-memory conclusions are supported. Different read times
can reflect file count, size distribution, content and cache effects; writer
identity is not itself a read-speed mechanism. No finding demonstrates a broken
framework or establishes a universal ingestion architecture.

## Related evidence

- [Harness](relation_fragments_20261028.py)
- [Summary and completeness checker](summarize_relation_fragments_20261028.py)
- [Dictionary baseline methodology](dict-fragments-20261028-methodology.md)
