# Typed-input NDJSON writes — DuckDB 1.5.2, 29 October 2026

**Native COPY won for both Arrow input types and the smaller pandas shape.
Direct Python writing won for the larger, deeply nested pandas shape.** This
result uses DuckDB exactly **1.5.2**, with 100,000 records in every measured case.
All 108 measured writes passed full-record validation and the independent audit.

## Write results at 256 KiB

Seconds are median [minimum–maximum] across three repetitions. These are complete
write paths, including conversion and COPY's connection lifecycle. No reads are
timed. Python batch size is fixed at 2,048 rows, not chosen from observed timings.

| Input | Shape | Rows | Python write seconds | COPY write seconds |
| --- | --- | ---: | --- | --- |
| pandas DataFrame | Smaller | 100,000 | 1.3855 [1.1162–1.4153] | **0.6531 [0.6153–0.8145]** |
| pandas DataFrame | Larger | 100,000 | **2.8117 [2.5275–2.9025]** | 4.1215 [3.8571–4.2400] |
| Arrow Table | Smaller | 100,000 | 0.9893 [0.8392–1.0156] | **0.1831 [0.1539–0.2127]** |
| Arrow Table | Larger | 100,000 | 2.4703 [2.4187–3.0374] | **0.7333 [0.6924–0.7721]** |
| Arrow RecordBatch | Smaller | 100,000 | 1.0922 [0.9703–1.1891] | **0.2440 [0.1760–0.2637]** |
| Arrow RecordBatch | Larger | 100,000 | 2.5673 [2.4677–2.7758] | **0.9368 [0.7540–1.4477]** |

At this target, COPY is 5.40×/3.37× faster for smaller/larger Arrow Tables and
4.48×/2.74× faster for RecordBatches. For pandas, COPY is 2.12× faster for smaller
records; direct Python is 1.47× faster for larger records. Across all three
targets, the same winner holds for each input/shape pair: COPY wins 15 of 18
groups, direct wins the three larger-pandas groups. Some three-run ranges overlap,
including larger pandas at 64 KiB; these are observed rankings, not significance
tests. [All targets, ranges and fragment sizes](results/typed-input-writes-20261029-152-2133ad9c40.tables.md)
are available without selecting the fastest batch or trial.

**Recommendation:** prefer native COPY for these 100,000-row Arrow inputs when
approximate fragment sizing is acceptable. For pandas, this evidence supports
COPY for the tested smaller shape but favors direct bounded Python writing for
the tested deeper, larger shape. It does not justify unconditional pandas COPY
or an automatic shape threshold. No adaptive dispatch implementation is made.
The experiment measures complete paths and cannot isolate the pandas conversion,
inference, serialization or file-management contribution to the difference.
Changed pandas/Arrow versions and fixtures also prevent attributing differences
from earlier reports solely to the DuckDB upgrade.

## Actual output at 256 KiB

Counts and sizes were identical across input types and all three repetitions.
The final short nonempty fragment is retained. COPY's larger fragments are an
accepted difference in this comparison, not enforcement of the requested limit.

| Shape | Path | Files | File bytes minimum / median / maximum | Empty files removed per trial |
| --- | --- | ---: | --- | ---: |
| Smaller | Python | 157 | 145,330 / 262,285 / 262,522 | 0 |
| Smaller | COPY | 49 | 698,255 / 843,179 / 843,236 | 1 |
| Larger | Python | 758 | 89,966 / 263,949 / 264,086 | 0 |
| Larger | COPY | 49 | 3,391,503 / 4,095,403 / 4,095,460 | 1 |

The full measured run checked **10,800,000 output records**, removed **45 transient
empty COPY files inside the timers**, and retained **zero empty fragments**.
Both paths emitted the same total bytes for each shape. Untimed checks also
validated all typed source records before each write.

## Fixtures and correctness contract

Every measured case starts with **100,000 records**, including both shapes and
all three input types. The inputs are separately constructed from identical
deterministic functions. Arrow arrays and nested schemas are explicitly typed.
Pandas uses int64, float64, bool, nullable Int64 and object columns.

| Shape | Root fields | Named field paths | Maximum container depth below root | Average UTF-8 NDJSON bytes per record | Total bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| Smaller | 11 | 13 | 1 | 410.69026 | 41,069,026 |
| Larger | 18 | 28 | 3 | 1,997.91256 | 199,791,256 |

Depth counts objects and arrays below the root; repeated event keys count once
in the named field paths. Byte counts include the final newline. Both shapes
contain Unicode, escaped text, booleans, finite floats, nullable integers,
nullable structs, nullable and empty lists, and null list elements.

A representative smaller record is:

```json
{
  "id": 1,
  "text": "café 東京 \"\\\nxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "number": 0.25,
  "optional": 1,
  "active": false,
  "nested": {"first": 1, "later": 2},
  "items": [1, null, 2],
  "category": "synthetic café / 東京",
  "description": "Readable payload — naïve résumé Readable payload — naïve résumé Readable payload — naïve résumé ",
  "flags": [true, false, null],
  "region": "Sydney"
}
```

The larger record retains these fields and adds this structure:

```json
{
  "details": {
    "owner": {
      "name": "Zoë 東京",
      "address": {"city": "Montréal", "postcode": 2000}
    },
    "events": [
      {"code": 1, "note": "arrived ✓"},
      {"code": null, "note": "café"}
    ]
  }
}
```

It also adds `field_0` through `field_5`; each contains the row and field numbers
followed by 24 repetitions of `é東京x`. The fixture artifact contains full,
unabbreviated examples for rows 0 and 1 of both shapes, including null cases.

Every source record and output record is checked by ID and a canonical SHA-256
digest. Validation rejects extra/missing fields, integer/float changes, string or
Unicode loss, list reordering, changed nulls, duplicates and missing IDs. Object
key order and whitespace are ignored. All validation reads are untimed.

Nullable pandas integers normalize to JSON integer/null; Arrow structs normalize
to fixed-field objects. Fixture objects already contain every declared field, so
there is no padding of missing keys. JSON does not preserve pandas/Arrow dtype
metadata. Floats are exact quarters; results do not establish correctness for
nonfinite or arbitrary floats, decimals, dates/timestamps, binary, categoricals,
arbitrary extension types, mixed scalar types or irregular dictionary schemas.

## Measurement

Exactly two complete paths are compared: input → DuckDB relation → native COPY
NDJSON, and input → bounded dictionaries → direct buffered Python UTF-8 NDJSON.
Python uses 2,048-row conversion batches, stdlib compact JSON, `ensure_ascii=False`
and `allow_nan=False`. There is no jsonlines or pandas-native JSON path.

Write timing includes conversion, fetching/scanning, serialization, directory and
file creation, write/flush/close, discovery/stat and transient empty-file cleanup.
COPY includes a new one-thread DuckDB connection and its close; Python does not
require a database connection. Fixture construction, source checks, garbage
collection and output validation are outside the timer. No reads or write/read
totals are timed. No fsync is forced.

Targets are 64 KiB, 256 KiB and 1 MiB. Python rolls over after a whole record reaches
the target. COPY's approximate sizing is accepted, without forcing equal file
counts. All completed fragments must be nonempty. Raw results retain every actual
file size and empty cleanup count. Validated synthetic output files are removed
after each trial, outside timing; prior artifacts are preserved.

Twelve discarded 1,000-row warmups cover every input/shape/path. Three measured
repetitions use fixed order seeds 17300–17302. The shared Windows OS byte-range
lock at `.tmp-fragment-benchmark-timing.lock` covers each trial's preparation,
write and validation. The harness is self-contained and imports no moving
production internals. The host otherwise remains uncontrolled, including other
development activity, caches and allocator reuse. These ranges describe three
observations, not statistical confidence. No memory, durability, cold-cache,
network-storage or read-speed conclusion follows.

## Environment and reproduction

Python **3.13.9**, DuckDB **1.5.2**, pandas **3.1.0**, PyArrow **26.0.0**,
NumPy **2.5.4**, Windows.
The harness prints and records all versions before trials and asserts DuckDB is
exactly 1.5.2. The executable is:

`E:\projects\assquack\benchmarks\.venv-typed-writes-152\Scripts\python.exe`

The project environment had DuckDB 1.5.2 but lacked pandas and Arrow. An isolated
environment was created with the project's Python and packages installed from
PyPI; no production dependency files or environments were changed. Network use
was limited to package installation. All benchmark data and execution are local
and synthetic. No credentials, `.env`, customer data or live systems were used.

```powershell
# To install the same main dependencies into an isolated Python 3.13 environment:
# uv pip install --python <isolated-python> duckdb==1.5.2 pandas==3.1.0 pyarrow==26.0.0 numpy==2.5.4
benchmarks/.venv-typed-writes-152/Scripts/python.exe benchmarks/typed_input_writes_20261029_152.py --run
poetry run python benchmarks/summarize_typed_input_writes_20261029_152.py benchmarks/results/typed-input-writes-20261029-152-2133ad9c40.jsonl
poetry run ruff check benchmarks/typed_input_writes_20261029_152.py benchmarks/summarize_typed_input_writes_20261029_152.py
poetry run pyright --project benchmarks/typed-input-writes-20261029-152-pyright.json
```

## Evidence

- [Harness](typed_input_writes_20261029_152.py),
  [methodology](typed-input-writes-20261029-152-methodology.md) and
  [independent checker](summarize_typed_input_writes_20261029_152.py).
- [Raw measured writes](results/typed-input-writes-20261029-152-2133ad9c40.jsonl),
  [environment and source hash](results/typed-input-writes-20261029-152-2133ad9c40.metadata.json)
  and [full fixture examples](results/typed-input-writes-20261029-152-2133ad9c40.fixtures.json).
- [Complete timing/fragment tables](results/typed-input-writes-20261029-152-2133ad9c40.tables.md),
  [summary JSON](results/typed-input-writes-20261029-152-2133ad9c40.summary.json) and
  [integrity audit](results/typed-input-writes-20261029-152-2133ad9c40.validation.json).
- [Smoke audit](results/typed-input-writes-20261029-152-c08bad27aa.validation.json):
  36 measured small-case writes plus 12 warmups passed, separate from main results.
- [Prior relation report](relation-fragments-20261028-report.md) and
  [prior typed-input report](typed-input-writes-20261028-report.md) supplied
  methodology only. The latter's DuckDB 1.4.3 timings are not accepted evidence
  for this version or pooled with this run.

## Checks and scope

Both scripts pass Ruff lint/format and scoped Pyright. Both full and smoke audits
verify version, script hash, complete matrix, full record counts and nonempty
fragments. The full audit also verifies each trial's total bytes against fixtures.
The report's JSON examples match the saved fixture values, and scoped Markdown
link checks pass. The repository-wide link checker and `git diff --check` also
pass at completion. An earlier link-check attempt encountered a non-UTF8 byte in
the concurrently edited epic document; that issue was resolved by the time of
the final check without changes to it by this diagnostic.
Production tests are not run because this diagnostic changes
no production behavior. No production edits, branches, commits, hidden agents or
new chats were created by this diagnostic; concurrent implementation edits were
left untouched.
