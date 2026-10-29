# Existing relation to NDJSON fragments — 28 October 2026

**Direct DuckDB COPY had the lowest write median in all 18 tested dataset/target
groups.** Converting the already prepared relation to bounded Python record
batches cost more in these tests. This is a relation-source result: it does not
establish dictionary-origin, pandas or Arrow performance and changes no
production implementation or architecture.

At the 262,144-byte target, COPY was about **9.2–9.9 times faster at writing**
100,000 narrow nested rows than the two Python routes using 2,048-row fetches.
For 10,000 wide nested rows it was about **3.4–3.5 times faster at writing**.
Those ratios compare complete write routes, including Python fetching and
conversion, actual file writes, flush/close and empty-output cleanup.

Readback was measured independently. Its rankings varied, with substantial range
overlap in several comparisons. No write/read totals are used in this report.
Native COPY's approximate rollover also produced substantially larger files than
the requested target; a strict fragment-size requirement would change which
routes are eligible.

## Results at the 256 KiB target

Each time is median [minimum–maximum] seconds across three fresh workers.
Python fetch batch size is fixed at 2,048 here, rather than selected after seeing
which batch won. Read includes a new DuckDB connection, full sampling and full
in-memory table materialization. It excludes validation and reader close.

Both datasets contain a nested struct, an integer/null list, explicit nulls,
booleans, late fields, Unicode and escaping. Wide adds twelve long Unicode
strings. All routes wrote exactly the same total bytes for each dataset.

| Nested dataset | Rows | Actual bytes | Route | Write seconds | Read seconds |
| --- | ---: | ---: | --- | --- | --- |
| Narrow | 100,000 | 24,104,177 | COPY | 0.104 [0.101–0.131] | 0.896 [0.862–0.926] |
| Narrow | 100,000 | 24,104,177 | jsonlines | 0.955 [0.951–1.138] | 0.970 [0.901–1.104] |
| Narrow | 100,000 | 24,104,177 | Direct UTF-8 buffer | 1.020 [0.882–1.148] | 1.195 [1.089–1.343] |
| Wide | 10,000 | 47,977,386 | COPY | 0.155 [0.154–0.157] | 0.530 [0.476–0.534] |
| Wide | 10,000 | 47,977,386 | jsonlines | 0.534 [0.527–0.592] | 0.559 [0.513–0.603] |
| Wide | 10,000 | 47,977,386 | Direct UTF-8 buffer | 0.543 [0.485–0.670] | 0.645 [0.498–0.998] |

The [complete 90-configuration table](results/relation-fragments-20261028-67174f6bd8.tables.md)
includes small, medium and large datasets; all three targets; and both 128- and
2,048-row Python fetch batches. The best observed write median was COPY in all
18 dataset/target groups; this is a descriptive ranking, not a significance test.

## Actual fragments and null padding

These are the same large datasets at 262,144 requested bytes. Counts are per
trial and were stable across all three repetitions. File-size statistics pool
the repetitions. Small nonempty final files are retained.

| Nested dataset | Route | Completed files | File bytes min / median / max | Transient empty files removed per trial |
| --- | --- | ---: | --- | ---: |
| Narrow, 100,000 rows | COPY | 49 | 412,004 / 495,467 / 497,516 | 1 |
| Narrow, 100,000 rows | Either Python route | 92 | 235,159 / 262,254 / 262,376 | 0 |
| Wide, 10,000 rows | COPY | 5 | 8,678,499 / 9,828,560 / 9,830,512 | 1 |
| Wide, 10,000 rows | Either Python route | 182 | 211,208 / 263,999 / 266,593 | 0 |

Across the full measured run, **39 transient empty files were removed**, all
before write completion and readback. Cleanup is included in write time. There
were **zero retained empty fragments** and no empty inference inputs. This does
not mean COPY avoided creating transient empty files.

Preparation materializes a typed relation before timing and therefore fills
missing struct fields with nulls. No additional writer null padding was allowed
or observed. Every writer was checked against the relation's actual values.

| Nested dataset | Rows | Actual bytes, every route | Added null keys during preparation |
| --- | ---: | ---: | ---: |
| Narrow | 1,000 | 229,108 | 2,886 |
| Narrow | 10,000 | 2,350,706 | 28,844 |
| Narrow | 100,000 | 24,104,177 | 288,438 |
| Wide | 100 | 473,004 | 90 |
| Wide | 1,000 | 4,779,788 | 2,886 |
| Wide | 10,000 | 47,977,386 | 28,844 |

## Observations, explanations and limits

Observed: COPY avoided the Python fetch/record-conversion/serialization route
and wrote faster in every dataset/target group. The experiment does not isolate
how much of that advantage comes from fetching, conversion, encoding or file
management; attributing the whole difference to any one component would be a
hypothesis. It also compares different rollover policies.

Observed: fewer files did not consistently mean faster reads. At the 1 MiB target,
the wide dataset's five COPY files read in a 0.508 s median; the 46-file Python
sets read in 0.400 s for jsonlines and 0.408 s for direct buffering with 2,048-row
fetches. Other configurations favored the COPY fragment set. jsonlines and direct
buffering also showed different read times despite matching recorded file counts,
size arrays and validated values. Writer identity itself does not control DuckDB
read speed. File boundaries, parser behavior, allocation, caching and uncontrolled
host activity are plausible influences; this run does not isolate their causes.

The host was Windows 11, Python 3.13.9, DuckDB 1.5.2 and jsonlines 4.0.0, with one
DuckDB thread. Workers used a shared OS-held exclusive lock, including preparation,
writing, reading and validation, to avoid overlapping the other diagnostic's
benchmark workload. The host otherwise remained uncontrolled. Trial order varied
with fixed seeds; three small warmups were discarded. Source preparation and
validation were excluded from timings. All routes started from the same fully
materialized table representation, with no lazy fixture construction charged to
one route. Python never needed a whole-input record list.

Writes used buffered filesystem I/O without forced `fsync`, consistent with the
baseline. Reads were warm-cache, particularly because fragment validation ran
first. Results do not cover durability, cold-cache, network storage, peak memory,
heterogeneous scalar-type drift or arbitrary JSON values. Three trials provide
observed ranges, not strong statistical confidence for close rankings. These
findings do not demonstrate that a framework is broken.

## Evidence and checks

- [Reproducible harness](relation_fragments_20261028.py),
  [methodology](relation-fragments-20261028-methodology.md) and
  [summary/completeness checker](summarize_relation_fragments_20261028.py).
- [Raw results](results/relation-fragments-20261028-67174f6bd8.jsonl),
  [environment and source hashes](results/relation-fragments-20261028-67174f6bd8.metadata.json),
  [summary JSON](results/relation-fragments-20261028-67174f6bd8.summary.json) and
  [validation record](results/relation-fragments-20261028-67174f6bd8.validation.json).
- All 270 measured trials passed: **5,494,500 records checked at each stage**
  against the prepared relation, with exact key/value/type hashes, unique and
  complete IDs, and identical source/readback schemas. The matrix contains exactly
  three repetitions for each of 90 configurations. An earlier 45-trial smoke run
  also passed; its timings are excluded from the report.
- Ruff lint/format, scoped Pyright, documentation links, benchmark-report links,
  terminology checks and `git diff --check` passed. Source hashes match the measured
  versions. Production tests were not run because no package behavior changed.

All nonempty fragments are preserved beneath
`.tmp-relation-fragments-20261028/67174f6bd8`; raw results contain each trial's path.
Existing files and baseline artifacts were preserved. No live systems, customer
data, credentials, branches or commits were used.
