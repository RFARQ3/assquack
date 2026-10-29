# Dictionary-to-NDJSON fragment results — 28 October 2026

Buffered Python writers were fastest for all 20 tested workload groups, both for
writing alone and for writing plus DuckDB inference/materialization. Direct
buffering had the lowest write median in 12 groups and jsonlines in 8; combined
medians split 10–10. This does not establish a universal winner between the two
Python implementations: small differences often overlap observed trial ranges.
Both DuckDB paths passed correctness, but converting already available dictionaries
to JSON, parsing/inferencing, then re-encoding with COPY cost more on this host.

**Recommendation:** use a buffered Python writer as the performance starting point
for dictionary-to-fragment ingestion, followed by DuckDB's full combined-fragment
inference. These results do not justify adding jsonlines solely for speed or
building a more complex buffering layer solely to beat jsonlines. If DuckDB COPY
is required, use bounded batches and tune their size independently of fragment
targets; the larger tested batches substantially reduced overhead. Whole-input
serialization remains a comparison baseline, not a DuckDB requirement or the
recommended solution for streaming inputs. No production implementation changed.

## Evidence and reproducibility

- [Harness](dict_fragments_20261028.py) and [summarizer](summarize_dict_fragments_20261028.py).
- [Methodology and published matrix](dict-fragments-20261028-methodology.md): full timing,
  memory, serialization, cache and correctness contracts.
- [Raw trial results](results/dict-fragments-20261028-611887bed6.jsonl),
  [metadata](results/dict-fragments-20261028-611887bed6.metadata.json),
  [summary CSV](results/dict-fragments-20261028-611887bed6.summary.csv),
  [summary JSON](results/dict-fragments-20261028-611887bed6.summary.json), and
  [completeness/source validation](results/dict-fragments-20261028-611887bed6.validation.json).

420 measured trials cover 140 configurations, with three fresh-worker repetitions
and four discarded warmups. All 7,923,300 source records were checked against both
fragment output and subsequent DuckDB table output. Four additional heterogeneous
semantic probes passed. Permitted changes are absent-to-null normalization and
exact numeric widening; non-null value loss is a failure. DuckDB COPY normalizes
keys and can increase output size. Python fragment output preserved the original
key sets. Full-sampling, union-by-name combined reading preserved every expected
late key and value. These checks cover JSON-compatible synthetic fixtures only.

The interrupted `6996de212a` run is explicitly marked ineligible and excluded.
After the completed run, only static typing annotations/comments/formatting and a
typing import changed in the harness; recursive function/class bytecode checks
confirm the measured algorithms are unchanged. Both source hashes are recorded.

## Large workloads at the 256 KiB target

Rows: 100,000 narrow or 10,000 wide. Each method uses its fastest observed batch
size for writing at this target. Times are medians in seconds; the range is all
three write trials. Setup/inference is included in read total, not added to it.
Read total includes full in-memory DuckDB table materialization. These cached
filesystem timings exclude generation, validation and forced disk synchronization.

| Shape | Path | Batch rows | Write s | Write range s | Setup/inference s | Read total s |
| --- | --- | --- | --- | --- | --- | --- |
| narrow | jsonlines | 128 | 0.496 | 0.476–0.524 | 0.246 | 0.760 |
| narrow | Direct buffer | 128 | 0.494 | 0.493–0.572 | 0.240 | 0.708 |
| narrow | DuckDB batch | 2048 | 3.588 | 3.164–3.669 | 0.240 | 0.785 |
| narrow | DuckDB whole | whole | 1.946 | 1.578–2.034 | 0.354 | 1.145 |
| wide | jsonlines | 2048 | 0.284 | 0.257–0.328 | 0.205 | 0.557 |
| wide | Direct buffer | 2048 | 0.258 | 0.243–0.347 | 0.268 | 0.807 |
| wide | DuckDB batch | 2048 | 2.053 | 1.909–2.175 | 0.180 | 0.506 |
| wide | DuckDB whole | whole | 1.441 | 1.435–1.554 | 0.152 | 0.440 |

The whole-input DuckDB path can read faster after producing fewer, larger files,
particularly for wide rows. That read saving did not overcome its write cost in
any workload group's combined median. Selecting the fastest writer batch does
not always select the fastest combined batch; both rankings appear below.

## Process memory

Same large workloads/selected batches as above; MiB of sampled process RSS,
including native DuckDB allocations. All workers import the same libraries,
including DuckDB, so these absolute baselines are not Python-writer-only deployment
footprints. Input-list memory is included in baseline and peak. Increment is the
median per-trial peak minus its own baseline, not subtraction of rounded medians.

| Shape | Path | Baseline MiB | Peak MiB | Increment MiB |
| --- | --- | --- | --- | --- |
| narrow | jsonlines | 103.1 | 103.2 | 0.1 |
| narrow | Direct buffer | 103.6 | 103.8 | 0.2 |
| narrow | DuckDB batch | 103.0 | 123.0 | 20.0 |
| narrow | DuckDB whole | 103.7 | 314.9 | 211.0 |
| wide | jsonlines | 100.3 | 100.6 | 0.3 |
| wide | Direct buffer | 100.3 | 100.7 | 0.4 |
| wide | DuckDB batch | 100.3 | 179.0 | 78.5 |
| wide | DuckDB whole | 100.4 | 353.6 | 253.4 |

Whole-input buffering is materially more memory-hungry. Bounded DuckDB keeps memory
below that baseline at the cost of repeated inference/COPY work. The medium-size
streaming experiments avoid prebuilding the complete input for bounded paths;
all streaming timings and memory results are in the summary tables. Streaming was
not tested at the largest size, so no measured large-streaming claim is made.
RSS polling can miss very short peaks and is not an exact allocation maximum.

## Fragment sizing and batching

Same large workloads/selected batches at a requested 262,144 bytes. File sizes are
minimum / median / maximum across the three trials, in bytes. Counts are medians.
Zero-byte files are retained in counts and are included among files below half
the target; no undersized output has been silently removed from these statistics.

| Shape | Path | Files | Empty files | Bytes min / median / max | Below half target |
| --- | --- | --- | --- | --- | --- |
| narrow | jsonlines | 74 | 0 | 73,081 / 262,266 / 262,330 | 1 |
| narrow | Direct buffer | 74 | 0 | 73,081 / 262,266 / 262,330 | 1 |
| narrow | DuckDB batch | 98 | 49 | 0 / 206,002 / 497,516 | 49 |
| narrow | DuckDB whole | 50 | 1 | 0 / 495,467 / 497,516 | 1 |
| wide | jsonlines | 179 | 0 | 233,673 / 265,927 / 266,248 | 0 |
| wide | Direct buffer | 179 | 0 | 233,673 / 265,927 / 266,248 | 0 |
| wide | DuckDB batch | 10 | 5 | 0 / 4,339,250 / 9,791,600 | 5 |
| wide | DuckDB whole | 7 | 1 | 0 / 9,809,303 / 9,830,512 | 1 |

Python rotation overshot the target by at most one complete record. DuckDB's
FILE_SIZE_BYTES did not impose a strict cap: wide output reached approximately
9.8 MB per file even with sub-megabyte targets. COPY also emitted empty trailing
files in some cases. Bounded COPY ends each batch independently, producing many
small files when the processing batch is smaller than the fragment target.

| Shape | DuckDB batch rows | Write s | Files | Below half target | Peak MiB |
| --- | --- | --- | --- | --- | --- |
| narrow | 128 | 14.045 | 782 | 782 | 118.2 |
| narrow | 2048 | 3.588 | 98 | 49 | 123.0 |
| wide | 128 | 3.868 | 157 | 79 | 118.6 |
| wide | 2048 | 2.053 | 10 | 5 | 179.0 |

The full three-target matrix and raw byte arrays are supplied in the results.
Larger targets generally reduce file-management overhead, but neither these
results nor FILE_SIZE_BYTES establish a universally optimal target or strict cap.

## Fastest observed eligible path by workload

These are minima of the per-configuration three-trial medians, not significance
tests. Path entries include processing batch rows. All candidates passed correctness.
The JSON/CSV summaries retain every candidate, trial range and memory metric.

| Shape | Rows | Source | Target KiB | Fastest write / batch | Write s | Fastest combined / batch | Combined s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| narrow | 1,000 | list | 64 | Direct buffer / 128 | 0.0055 | jsonlines / 128 | 0.0601 |
| narrow | 1,000 | list | 256 | Direct buffer / 2048 | 0.0046 | Direct buffer / 2048 | 0.0632 |
| narrow | 1,000 | list | 1024 | jsonlines / 2048 | 0.0044 | jsonlines / 2048 | 0.0585 |
| narrow | 10,000 | list | 64 | Direct buffer / 128 | 0.0482 | jsonlines / 128 | 0.1803 |
| narrow | 10,000 | list | 256 | jsonlines / 128 | 0.0463 | jsonlines / 128 | 0.1664 |
| narrow | 10,000 | list | 1024 | jsonlines / 128 | 0.0475 | jsonlines / 128 | 0.1670 |
| narrow | 10,000 | stream | 256 | Direct buffer / 128 | 0.0445 | Direct buffer / 128 | 0.1536 |
| narrow | 100,000 | list | 64 | Direct buffer / 128 | 0.5705 | Direct buffer / 128 | 1.4272 |
| narrow | 100,000 | list | 256 | Direct buffer / 128 | 0.4938 | Direct buffer / 2048 | 1.1635 |
| narrow | 100,000 | list | 1024 | Direct buffer / 128 | 0.4597 | jsonlines / 128 | 1.1176 |
| wide | 100 | list | 64 | jsonlines / 2048 | 0.0046 | jsonlines / 128 | 0.0625 |
| wide | 100 | list | 256 | jsonlines / 128 | 0.0026 | Direct buffer / 2048 | 0.0624 |
| wide | 100 | list | 1024 | jsonlines / 128 | 0.0023 | jsonlines / 128 | 0.0577 |
| wide | 1,000 | list | 64 | jsonlines / 128 | 0.0390 | jsonlines / 2048 | 0.1899 |
| wide | 1,000 | list | 256 | Direct buffer / 128 | 0.0275 | Direct buffer / 2048 | 0.1407 |
| wide | 1,000 | list | 1024 | jsonlines / 2048 | 0.0223 | jsonlines / 128 | 0.1102 |
| wide | 1,000 | stream | 256 | Direct buffer / 128 | 0.0239 | Direct buffer / 128 | 0.1201 |
| wide | 10,000 | list | 64 | Direct buffer / 2048 | 0.3570 | Direct buffer / 2048 | 1.3913 |
| wide | 10,000 | list | 256 | Direct buffer / 2048 | 0.2581 | Direct buffer / 128 | 0.8188 |
| wide | 10,000 | list | 1024 | Direct buffer / 2048 | 0.2063 | Direct buffer / 2048 | 0.5962 |

## Validation and limits

Harness correctness gates, matrix completeness, Ruff lint/format, and scoped
Pyright checking passed. Pyright uses local dependency paths and narrowly justified
suppressions for jsonlines' generated constructor, DuckDB's file-list overload,
and the writer's method-branch invariant. Production tests were not run: this
research adds benchmark tooling only and changes no production code or contracts.
Repository whitespace and local documentation-link checks are recorded in the
handoff. No live systems, customer data, .env files, commits or pushes were used.

Results describe one Windows host, Python 3.13.9 and DuckDB 1.5.2 with one DuckDB
thread. Validation warms the read cache; no cold-cache, fsync/durability or network
filesystem performance is claimed. Background host activity was uncontrolled.
Three trials are sufficient to expose the large architecture differences here,
but close jsonlines/direct rankings should be treated as near ties.
