# Typed-input NDJSON write benchmark — 28 October 2026

**For the tested large Arrow inputs, native relation/COPY was the fastest complete
write path. Pandas depended on shape: its own JSON writer led for large narrow
records; COPY led for large wide records at the 256 KiB target.** Small inputs
often favored direct writers. There is no universal winner.

These are write-only results on DuckDB **1.4.3**, not the repository's supported
1.5+ environment. Treat the paths below as candidates to reconfirm on the target
version, not a production change recommendation. No reads were timed.

## Large inputs at a 262,144-byte target

Three-trial median [minimum–maximum] write seconds. Python routes use a fixed
2,048-row batch here, not the fastest batch selected retrospectively.

| Input | Nested shape / rows | Native relation + COPY write s | Python records + buffered bytes write s | Pandas JSON + buffered bytes write s |
| --- | --- | --- | --- | --- |
| DataFrame | Narrow / 100,000 | 0.6461 [0.5736–0.8087] | 0.8809 [0.8236–0.8917] | **0.3237 [0.2829–0.7048]** |
| DataFrame | Wide / 10,000 | **0.2923 [0.2767–0.3094]** | 0.4571 [0.4141–0.5222] | 0.5248 [0.5065–0.5766] |
| Arrow Table | Narrow / 100,000 | **0.0989 [0.0880–0.1680]** | 1.4141 [1.3297–1.4297] | — |
| Arrow Table | Wide / 10,000 | **0.1684 [0.1611–0.1790]** | 0.6226 [0.5929–0.6719] | — |
| Arrow RecordBatch | Narrow / 100,000 | **0.0915 [0.0849–0.0946]** | 1.4430 [1.3715–1.5951] | — |
| Arrow RecordBatch | Wide / 10,000 | **0.1799 [0.1493–0.1905]** | 0.6766 [0.6360–0.8864] | — |

COPY is about 14.3×/15.8× faster than bounded Python conversion for large narrow
Table/RecordBatch inputs here, and 3.7×/3.8× for wide ones. Pandas JSON has about
half COPY's narrow median, but their ranges overlap substantially. These ratios
describe full routes, including conversion and native connection lifecycle.

Across all 18 dataset/target groups per source, the lowest observed median among
all routes/batches was pandas JSON in 12 DataFrame groups, direct Python records
in four, and COPY in two. For Table it was COPY in 13 and Python records in five;
for RecordBatch it was COPY in 15 and Python records in three. Considering only
the requested common pair, pandas Python records led 13 groups and COPY five.
These counts select the best observed batch per route and are descriptive,
not statistically established boundaries or automatic dispatch thresholds.

Prefer native COPY as the candidate for larger Arrow fixtures when approximate
sizing is acceptable. For DataFrames, retain a shape/size-dependent choice in
further testing: pandas JSON is promising for the tested narrow values, while
COPY is promising for wide large records. Its float precision contract prevents
recommending pandas JSON for arbitrary source values. For small inputs, measure
the direct route before paying native connection/registration cost. No result
establishes behavior for pandas/Arrow types outside the fixture contract below.

## Actual output and validation

Every large narrow route above wrote 20,069,026 bytes; every large wide route
wrote 47,571,214 bytes. Narrow COPY retained 49 files (342,095–413,156 bytes),
versus 77 direct files (137,977–262,347 bytes). Wide COPY retained five files
(8,605,148–9,747,414 bytes), versus 179 direct files (152,299–266,560 bytes).
Each large COPY trial removed one transient empty file before ending write time.
Pandas JSON and Python records had the same layouts here. The
[detailed large-case table](results/typed-input-writes-20261028-fb1347db68.table.md)
includes per-file medians; the full CSV includes every configuration.

All **594 measured trials / 198 configurations** passed complete input/output
validation, checking **12,087,900 output records**. There were **zero retained
empty fragments** and **117 transient empty files removed** in measured trials.
The final-source smoke repeat also passed: 66 measured trials, 22 configurations,
36,300 output records and zero retained empty fragments. Smoke results are not
pooled into the main timing results.

## Scope and measurement

Pandas DataFrame, PyArrow Table and PyArrow RecordBatch start as separate,
explicitly typed inputs. Narrow and wide have the same nullable nested struct,
nullable integer list, nullable integer, finite float, boolean, integer identity
and Unicode/escaped text. Wide adds twelve long text columns. Arrow fixtures are
constructed from typed column arrays, not from a dictionary record dataset.

The common routes are native relation registration followed by DuckDB COPY, and
bounded Python records followed by compact stdlib JSON and buffered UTF-8 binary
writes. The additional pandas route uses its own bounded `to_json` implementation,
documented [before measurement](typed-input-writes-20261028-methodology.md).
No jsonlines route is used.

Write time starts before conversion/registration. It includes a new DuckDB
connection and close for COPY, lazy source scanning, conversion, serialization,
actual file writes, flush/close, file discovery and removal of transient empty
COPY files. Python opens a fragment only for a record and closes it after a whole
record reaches the byte target. Both routes retain nonempty short final files.
No fsync is performed. Initial fixture construction, source verification,
garbage collection and complete output validation are excluded for every route.

The matrix uses narrow 1,000/10,000/100,000 and wide 100/1,000/10,000 rows;
65,536/262,144/1,048,576-byte targets; and 128/2,048-row Python batches. Each of
198 configurations has three repetitions, shuffled with seeds 17300–17302 after
22 discarded small-case warmups covering source, shape, writer and batch.
One process creates a new fixture, output directory and native connection per
trial. Every workload holds the OS byte-range exclusive lock at
`.tmp-fragment-benchmark-timing.lock`; waiting is outside timing.

## Correctness contract

Full source values are checked before writing, and every output record is checked
after timing using its unique integer identity and canonical JSON SHA-256 digest.
Duplicate or missing identities fail validation. Canonicalization preserves
integer versus floating-point JSON representations, booleans, all object keys,
nesting, array order, strings and nulls. No missing-key padding or numeric widening
is permitted. Nullable pandas integers normalize to JSON integer/null; fixed-field
Arrow structs normalize to JSON objects with the same fields. JSON object key order
and whitespace are irrelevant. Output row order is not required.

Floats are exact multiples of one quarter. Thus pandas' `double_precision=15`
passes this fixture but cannot be assumed lossless for arbitrary floating-point
inputs. No claim extends to NaN, infinity, decimals, dates/timestamps, binary,
categoricals, heterogeneous Python objects or arbitrary pandas extension types.
Only full-value-correct routes can be recommended.

## Environment and fairness limits

Python 3.11.9, DuckDB **1.4.3**, pandas 2.3.3 and PyArrow 16.1.0 on Windows,
using an existing local diagnostic environment. DuckDB uses one thread throughout.
No dependencies were installed or changed. The repository requires DuckDB 1.5+
and the earlier reports used 1.5.2; these results are therefore an environment-
specific diagnostic, not validation of the production-supported dependency set.
Do not compare the durations against earlier reports as an isolated source-type
effect. Reconfirmation on the production version is needed before adopting a path.

COPY's approximate rollover can produce much larger fragments than requested.
This is an accepted eligibility difference here; the routes are not matched on
file count or maximum file size. Python enforces whole-record target rollover.
Native and pandas use their own serializers; stdlib uses `ensure_ascii=False`,
compact separators and `allow_nan=False`. Equivalent values do not require
byte-identical serialization. This comparison measures complete practical routes,
not serialization alone. COPY alone bears connection setup/teardown cost.

Validation warms input caches. Repetition shares process/allocator and OS caches;
there is no cold-cache control. Background host load remains uncontrolled despite
the shared lock. Three-trial ranges describe observations, not statistical proof.
Memory was not sampled because psutil is absent from the compatible existing
environment; no new memory harness or dependency installation was introduced.
Validated synthetic output fragments are removed after each trial; raw results
retain actual bytes, every file size/count, schema and transient-empty counts.

## Reproduction

```powershell
.tmp-mad-parquet-repro/venv/Scripts/python.exe benchmarks/typed_input_writes_20261028.py --run
poetry run python benchmarks/summarize_typed_input_writes_20261028.py benchmarks/results/<run>.jsonl
poetry run ruff check benchmarks/typed_input_writes_20261028.py benchmarks/summarize_typed_input_writes_20261028.py
poetry run pyright --project benchmarks/typed-input-writes-20261028-pyright.json
```

The harness needs the listed dependencies; its runtime is explicit rather than
modifying the project's Poetry environment. `--smoke` exercises all routes at
small row counts and the smallest target. The summarizer checks script hash,
complete matrix/repetition coverage, record counts, byte/file sums, nonempty
fragments and stable per-configuration file-size distributions.

No production code, existing documentation, commits or branches were changed.
No credentials, `.env` files, customer data or live services were accessed.

## Artifacts and checks

- [Harness](typed_input_writes_20261028.py) and [summarizer](summarize_typed_input_writes_20261028.py)
- [Raw measured trials](results/typed-input-writes-20261028-fb1347db68.jsonl)
- [Environment and full matrix](results/typed-input-writes-20261028-fb1347db68.metadata.json)
- [All configuration summaries](results/typed-input-writes-20261028-fb1347db68.summary.csv)
- [Integrity and coverage checks](results/typed-input-writes-20261028-fb1347db68.validation.json)
- [Final-source smoke checks](results/typed-input-writes-20261028-effa6693a3.validation.json)
- [Earlier dictionary report](dict-fragments-no-empty-20261028-report.md) and [relation report](relation-fragments-20261028-report.md), used only as methodology references

Both scripts passed Ruff lint/format and scoped Pyright checks. Full-run and
final-source smoke summarization passed source-hash, matrix, repetition, record,
byte and fragment checks. `git diff --check` passed. Production tests were not
run because no production behavior was changed. Validation on DuckDB 1.5+ and
memory measurements were not performed in this existing environment.
