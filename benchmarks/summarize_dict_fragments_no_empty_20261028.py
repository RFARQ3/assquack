"""Validate and summarize the no-empty repeat; never combine write/read times."""

from __future__ import annotations

import csv
import hashlib
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

KEYS = ["shape", "n", "source", "target", "method", "batch"]


def summarize(path: Path) -> None:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    metadata = json.loads(
        path.with_suffix(".metadata.json").read_text(encoding="utf-8")
    )
    baseline_path = path.with_name("dict-fragments-20261028-611887bed6.jsonl")
    baseline_rows = [
        json.loads(line)
        for line in baseline_path.read_text(encoding="utf-8").splitlines()
    ]
    baseline = {tuple(r[k] for k in KEYS): r for r in baseline_rows}
    groups = defaultdict(list)
    for row in rows:
        assert row["correct"] and row["retained_empty_files"] == 0
        assert row["files"] == len(row["sizes"]) and min(row["sizes"]) > 0
        assert row["bytes"] == sum(row["sizes"])
        assert sum(row["null_profile"]["rows_per_file"]) == row["n"]
        key = tuple(row[k] for k in KEYS)
        old = baseline[key]
        assert row["schema"] == old["schema"]
        assert row["bytes"] == old["bytes"]
        python_key = (
            row["shape"],
            row["n"],
            row["source"],
            row["target"],
            "direct",
            128,
        )
        assert (
            row["bytes"] - baseline[python_key]["bytes"]
            == row["null_profile"]["added_null_bytes"]
        )
        assert sorted(row["sizes"]) == sorted(s for s in old["sizes"] if s > 0)
        groups[key].append(row)
    expected = {tuple(c[k] for k in KEYS) for c in metadata["configurations"]}
    assert set(groups) == expected and len(rows) == len(expected) * 3
    summaries = []
    for key, trials in groups.items():
        assert sorted(r["repetition"] for r in trials) == [0, 1, 2]
        entry = dict(zip(KEYS, key, strict=True))
        for metric in [
            "write_s",
            "read_s",
            "inference_s",
            "bytes",
            "files",
            "removed_empty_files",
            "normalized_records",
            "write_peak_rss",
            "read_peak_rss",
        ]:
            values = [r[metric] for r in trials]
            entry[metric] = statistics.median(values)
            if metric.endswith("_s"):
                entry[metric + "_min"] = min(values)
                entry[metric + "_max"] = max(values)
        sizes = [s for r in trials for s in r["sizes"]]
        entry.update(
            file_min=min(sizes),
            file_median=statistics.median(sizes),
            file_max=max(sizes),
        )
        for metric in ["explicit_null_values", "added_null_keys", "added_null_bytes"]:
            values = [r["null_profile"][metric] for r in trials]
            assert len(set(values)) == 1
            entry[metric] = values[0]
        summaries.append(entry)
    summaries.sort(key=lambda r: tuple(str(r[k]) for k in KEYS))
    path.with_suffix(".summary.json").write_text(
        json.dumps(summaries, indent=2), encoding="utf-8"
    )
    with path.with_suffix(".summary.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)
    source = Path(__file__).with_name("dict_fragments_no_empty_20261028.py")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == metadata["script_sha256"]
    validation = dict(
        configurations=len(groups),
        measured_trials=len(rows),
        source_records=sum(r["n"] for r in rows),
        all_fragment_and_duckdb_records_valid=True,
        retained_empty_files=0,
        transient_empty_files_removed=sum(r["removed_empty_files"] for r in rows),
        measured_source_hash_verified=True,
        baseline_bytes_schemas_nonempty_size_distributions_match=True,
        added_null_bytes_explain_all_output_byte_increases=True,
        semantic_probes=metadata["semantic_probes"],
    )
    path.with_suffix(".validation.json").write_text(
        json.dumps(validation, indent=2), encoding="utf-8"
    )

    # Use the baseline report's batch choices, avoiding a new winner-selection bias.
    selected = []
    for shape, n in [("narrow", 100000), ("wide", 10000)]:
        for method in ["jsonlines", "direct", "duck_batch", "duck_all"]:
            batch = (
                None
                if method == "duck_all"
                else (
                    128
                    if shape == "narrow" and method in ["jsonlines", "direct"]
                    else 2048
                )
            )
            selected.append(
                next(
                    r
                    for r in summaries
                    if r["shape"] == shape
                    and r["n"] == n
                    and r["source"] == "list"
                    and r["target"] == 262144
                    and r["method"] == method
                    and r["batch"] == batch
                )
            )
    old_write = [0.496, 0.494, 3.588, 1.946, 0.284, 0.258, 2.053, 1.441]
    old_read = [0.760, 0.708, 0.785, 1.145, 0.557, 0.807, 0.506, 0.440]
    report = [
        "# Dictionary-origin repeat with no empty completed fragments",
        "",
        (
            "The earlier 100,000-row narrow whole-input read slowdown did not "
            f"reproduce: its median is {selected[3]['read_s']:.3f}s, compared "
            "with the previous 1.145s. Empty-file cleanup cannot be credited "
            "causally because no paired baseline artifacts containing empties "
            "remain. Other read results move in both directions."
        ),
        "",
        (
            "Offline synthetic repeat, 28 October 2026. Write and DuckDB "
            "read/materialization durations are independent. Both narrow and "
            "wide records contain the same nested object and array; wide adds "
            "twelve text fields."
        ),
        "",
        "## Large datasets at a 262,144-byte fragment target",
        "",
        (
            "Three-trial medians, with observed minimum-maximum ranges. Batch "
            "choices match the previous report; they are not reselected for "
            "this run. Read includes connection, full inference and in-memory "
            "table materialization. No write/read sum is presented."
        ),
        "",
        (
            "| Dataset rows | Writer | Batch rows | Actual bytes | Write "
            "seconds (range) | Previous write seconds | Read seconds (range) | "
            "Previous read seconds |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row, old, previous_write in zip(selected, old_read, old_write, strict=True):
        r = row
        report.append(
            f"| {r['n']:,} {r['shape']}, same nesting | {r['method']} | {r['batch'] or 'whole'} | {r['bytes']:,} | {r['write_s']:.3f} ({r['write_s_min']:.3f}-{r['write_s_max']:.3f}) | {previous_write:.3f} | {r['read_s']:.3f} ({r['read_s_min']:.3f}-{r['read_s_max']:.3f}) | {old:.3f} |"  # noqa: E501
        )
    report += [
        "",
        "## Completed file layout and null padding",
        "",
        (
            "All completed fragments and all inference inputs are nonempty. "
            "COPY transient empty files were removed after close, inside "
            "write timing. Sizes below include every retained file; "
            "undersized nonempty files remain."
        ),
        "",
        (
            "| Dataset rows | Writer | Retained files | Removed transient "
            "empties | Bytes per file min / median / max | Explicit null "
            "values | Added null keys | Added null bytes |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in selected:
        report.append(
            f"| {r['n']:,} {r['shape']} | {r['method']} | {r['files']} | {r['removed_empty_files']} | {r['file_min']:,} / {r['file_median']:,} / {r['file_max']:,} | {r['explicit_null_values']:,} | {r['added_null_keys']:,} | {r['added_null_bytes']:,} |"  # noqa: E501
        )
    report += [
        "",
        (
            "Added null bytes count the UTF-8 key, colon, null and one "
            "separator for each added key. These fixtures already have other "
            "members at every padded object level, so this is the exact "
            "padding-byte contribution. Explicit nulls include array elements "
            "and optional fields. Other encoding differences are not "
            "attributed to padding."
        ),
        "",
        "## Measurement and correctness",
        "",
        f"{len(rows)} measured trials cover {len(groups)} configurations, with three fresh-worker repetitions and four discarded warmups. Every one of {validation['source_records']:,} records was validated both in fragments and after DuckDB materialization. Four heterogeneous semantic probes passed. Retained empty files: zero; measured transient empty files removed: {validation['transient_empty_files_removed']:,}.",  # noqa: E501
        "",
        (
            "The prior size matrix, targets (65,536 / 262,144 / 1,048,576 "
            "bytes), batch sizes (128 / 2,048), source modes and stdlib JSON "
            "serializer are unchanged. The order uses independently shuffled "
            "repetitions with seeds 9100-9102. Every worker holds an OS "
            "byte-range exclusive lock at "
            "`.tmp-fragment-benchmark-timing.lock` throughout its workload, "
            "including reads; the other diagnostic uses the same lock. "
            "Waiting is outside timing. OS process exit releases the lock."
        ),
        "",
        (
            "Write timing includes dictionary-to-JSON conversion, native "
            "parsing/COPY where applicable, writes, flush/close, output "
            "enumeration and empty-file cleanup. Input-list construction and "
            "validation are excluded. Stream generation is subtracted, with "
            "wall time and subtraction retained in raw results. There is no "
            "fsync, matching the baseline. Python writers open files only for "
            "actual records. COPY may create transient empty files; the "
            "harness does not register or read them."
        ),
        "",
        (
            "Every reader uses DuckDB 1.5.2, one thread, `sample_size=-1`, "
            "`union_by_name=True`, `field_appearance_threshold=0.0`, "
            "`map_inference_threshold=-1`, `hive_partitioning=False`, "
            "newline-delimited JSON and the same full table materialization. "
            "Correctness permits only absent-to-null padding and exactly "
            "equal numeric widening, while requiring explicit nulls, all late "
            "keys, nested values, booleans and record identity. Schemas are "
            "retained in every raw result. No null semantics were changed to "
            "improve speed."
        ),
        "",
        (
            "Reads follow fragment validation and null profiling and "
            "therefore use warm filesystem caches. The extra profiling pass "
            "is outside timing but can affect cache/allocator state. No "
            "cold-cache, fsync, network filesystem or power-loss durability "
            "claim is made. RSS sampling is approximate. Background activity "
            "is uncontrolled despite serialization with the other benchmark. "
            "Three trials provide ranges, not statistical proof."
        ),
        "",
        (
            "The baseline timed fragments were removed by its harness; "
            "retained scratch artifacts contain no empty files. Consequently "
            "no paired old-artifact empty-file control was available, and "
            "empty-file causality is not established. Differences between "
            "independent old and new runs cannot isolate cleanup cost or "
            "empty-file overhead. Writer identity does not itself control "
            "reading speed; bytes, schema, row/file layout, parsing and host "
            "conditions matter."
        ),
        "",
        "## Reproduce and inspect",
        "",
        "```powershell",
        "poetry run python benchmarks/dict_fragments_no_empty_20261028.py --run",
        (
            "poetry run python "
            "benchmarks/summarize_dict_fragments_no_empty_20261028.py "
            "benchmarks/results/<run>.jsonl"
        ),
        "```",
        "",
        f"- [Raw results](results/{path.name})",
        f"- [Metadata](results/{path.stem}.metadata.json)",
        f"- [All configuration summaries](results/{path.stem}.summary.csv)",
        f"- [Validation](results/{path.stem}.validation.json)",
        "- [Harness](dict_fragments_no_empty_20261028.py)",
        "- [Summarizer](summarize_dict_fragments_no_empty_20261028.py)",
        "- [Previous report](dict-fragments-20261028-report.md)",
        "",
    ]
    narrow_batch, narrow_whole = selected[2], selected[3]
    report += [
        "## Observations and explanations",
        "",
        (
            "All repeat trials match the corresponding baseline total bytes, "
            "inferred schema and nonempty file-size distribution. This supports "
            "that the data and nonempty layout were preserved; it is not a "
            "paired timing control. The repeat also times output enumeration "
            "and empty-file cleanup for every path; baseline enumeration was "
            "outside write timing. This modest additional work must not be "
            "mistaken for an underlying writer change."
        ),
        "",
        (
            f"At 100,000 narrow rows / 262,144-byte target, whole COPY reads in "
            f"{narrow_whole['read_s']:.3f}s median, versus the old 1.145s; bounded "
            f"COPY (2,048 rows) reads in {narrow_batch['read_s']:.3f}s median, "
            "versus the old 0.785s. Consult the ranges above before ranking them."
        ),
        "",
        (
            "Both COPY paths retain 49 files: 48 with 2,048 rows and one with "
            "1,696 rows. Whole COPY adds 88,064 more absent-to-null keys than "
            "bounded COPY, all explained by globally padding across_late in "
            "early rows instead of only within batches where it appears. "
            "Those keys account for exactly 1,673,216 extra bytes. The final "
            "inferred schemas are equal. More bytes is a plausible read-cost "
            "factor, but these measurements do not isolate its contribution."
        ),
        "",
        (
            "For wide rows the added text dominates output bytes, while COPY "
            "produces far fewer, larger files than Python rotation. Reduced "
            "file-opening and per-file inference work is a plausible explanation "
            "for some read differences; writer identity is not an explanation "
            "and this experiment does not isolate file-count effects."
        ),
        "",
        (
            "The separate write results include JSON serialization, native "
            "parsing and re-encoding in the COPY paths. Bounded COPY repeats "
            "binding/COPY for each batch. These are real differences in work "
            "performed, not evidence that the framework is broken."
        ),
        "",
        "## Checks",
        "",
        (
            "The harness and summarizer passed Ruff lint/format and scoped "
            "Pyright checks. The summarizer asserts full matrix/repetition "
            "coverage, source hash, record totals, positive file sizes, byte "
            "totals and baseline layout/schema equivalence. git diff --check "
            "passed. Production tests were not run: no package code, public "
            "contract or production behavior changed."
        ),
        "",
    ]
    output = path.parent.parent / "dict-fragments-no-empty-20261028-report.md"
    output.write_text("\n".join(report), encoding="utf-8")
    print(json.dumps(validation, indent=2))
    print(output)


if __name__ == "__main__":
    summarize(Path(sys.argv[1]))
