# Typed-input writes on DuckDB 1.5.2

This offline diagnostic compares exactly two complete write paths: an existing
pandas DataFrame, PyArrow Table or RecordBatch becomes a DuckDB relation followed
by native JSON COPY; or bounded Python dictionaries followed by compact stdlib
JSON encoded to buffered UTF-8 files. It imports no production Assquack internals.
No reads are timed. No third writer or automatic dispatch policy is tested.

Both deterministic shapes contain 100,000 rows for every input and target.
Smaller has 11 root fields, 13 distinct named field paths, and at most one
container level below the root. Larger has 18 root fields, 28 distinct named field
paths, and at most three container levels below the root. Its owner/address
objects and event records add depth; six Unicode strings add payload. Lists count
as containers, and repeated event keys count once in named field paths.

Both shapes contain actual Unicode, escaped text, booleans, int64 values, finite
float64 values, nullable integers, nullable structs, nullable/empty lists and
null list elements. Every nested object has its declared fields. Each input is
constructed separately from identical deterministic field functions. Arrow arrays
and nested schemas have explicit types; pandas uses int64, float64, bool,
nullable Int64 and object columns. No dictionary-to-Arrow schema inference occurs.

Before trials, the harness prints and saves Python, executable, DuckDB, pandas and
Arrow versions and asserts DuckDB is exactly 1.5.2. It also saves readable examples
for rows 0 and 1, field counts, depth and full-dataset serialized byte averages.
These bytes use compact UTF-8 JSON plus a newline, not Python object memory size.

The matrix has three inputs, two shapes, three targets (65,536, 262,144 and
1,048,576 bytes), two paths and three repetitions: 108 measured writes. Python
conversion uses 2,048-row batches throughout. Twelve discarded 1,000-row warmups
cover every input/shape/path; measured order varies using seeds 17300–17302.
DuckDB uses one thread. A single process creates new inputs for each trial and a
fresh connection for each COPY write. The shared Windows OS byte-range lock at
`E:/projects/assquack/.tmp-fragment-benchmark-timing.lock` covers fixture preparation,
write and validation. Waiting for it is outside timing.

Write timing includes connection creation and close for COPY, relation creation,
all source scanning/conversion, Python dictionary conversion, serialization,
directory creation, file writes, flush/close, output discovery/stat and removal
of transient empty COPY files. Python requires no database connection. Input
construction, input correctness checks, garbage collection and output validation
are excluded consistently. There is no forced fsync. Python opens files lazily
and rolls over only after a complete record reaches the target. COPY uses its
native approximate sizing; file counts and sizes are deliberately not matched.
All retained fragments must be nonempty. Empty removal counts and every file size
are recorded in raw results. Nonempty fragments are deleted after validation,
outside timing, within the invocation's verified scratch directory only.

Every input row is checked before writing against its deterministic expected
SHA-256 digest. After writing, every output row is parsed without timing, checked
for a unique valid ID, and matched to that expected digest. Canonical JSON sorts
keys but retains field presence, integer versus float spelling, booleans, strings,
nulls, nesting and list order. All IDs must be present. No schema or value loss is
accepted. JSON object key order and whitespace are irrelevant. Nullable pandas
integers normalize to integer/null; Arrow structs normalize to fixed-field
objects. Values are already compatible with these normalizations, so they add
no missing keys. No pandas native JSON path is included.

Floats are exact quarters. Results do not establish fidelity for nonfinite or
arbitrary floats, decimals, timestamps, binary, categoricals, arbitrary extension
types, mixed scalar types or irregular dictionary schemas. JSON output does not
retain pandas/Arrow dtype metadata. Full-value correctness here is not a claim
that JSON preserves every source-type contract.

These are local buffered write measurements on a shared Windows host. Cache and
allocator reuse, background host load and different fragment policies remain
limitations. Three-trial ranges are descriptive, not confidence intervals. No
memory, durability, cold-cache, network-storage or read-performance claim follows.

## Related evidence

- [Harness](typed_input_writes_20261029_152.py)
- [Independent matrix checker](summarize_typed_input_writes_20261029_152.py)
- [Prior relation methodology](relation-fragments-20261028-report.md)
- [Prior typed-input report](typed-input-writes-20261028-report.md): its DuckDB
  1.4.3 results are not accepted version evidence for this experiment.
