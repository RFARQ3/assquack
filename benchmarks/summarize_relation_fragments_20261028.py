"""Validate completed raw trials and render separate write/read tables."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import relation_fragments_20261028 as benchmark
from relation_fragments_20261028 import configurations, summarize


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.results.read_text().splitlines()]
    metadata = json.loads(args.results.with_suffix(".metadata.json").read_text())
    assert not metadata["smoke"]
    assert metadata["threads"] == 1 and metadata["fsync"] is False
    assert (
        metadata["script_sha256"]
        == hashlib.sha256(Path(benchmark.__file__).read_bytes()).hexdigest()
    )
    assert (
        metadata["baseline_sha256"]
        == hashlib.sha256(Path(benchmark.baseline.__file__).read_bytes()).hexdigest()
    )
    keys = ("shape", "n", "target", "method", "batch")
    expected = {tuple(c[key] for key in keys) for c in configurations()}
    actual = {tuple(row[key] for key in keys) for row in rows}
    assert actual == expected and len(rows) == len(expected) * 3
    for config in expected:
        matching = [row for row in rows if tuple(row[key] for key in keys) == config]
        assert sorted(row["repetition"] for row in matching) == [0, 1, 2]
    for row in rows:
        assert row["correct"] and row["retained_empty_files"] == 0
        assert min(row["sizes"]) > 0
        assert row["files"] == len(row["sizes"])
        assert row["bytes"] == sum(row["sizes"])
        assert row["schema"] == row["read_schema"]
    summarize(rows, args.results)
    summary = json.loads(args.results.with_suffix(".summary.json").read_text())
    lines = [
        "# Relation-source measurements — 28 October 2026",
        "",
        "Three trials per row. Times are median [minimum-maximum] seconds.",
        "Write and read are separate measurements. Read includes full sampling",
        "and table materialization. Each shape has a struct and integer/null list;",
        "wide adds twelve long Unicode string columns. File sizes pool all trials.",
        "",
        "| Nested dataset | Rows | Target bytes | Route | Fetch batch rows | "
        "Actual bytes | Files | Write seconds | Read seconds | "
        "File bytes min / median / max | Transient empties removed | "
        "Added null keys during preparation |",
        "| --- | ---: | ---: | --- | ---: | ---: | ---: "
        "| --- | --- | --- | ---: | ---: |",
    ]

    def duration(item, name):
        metric = item[name]
        return f"{metric['median']:.4f} [{metric['min']:.4f}-{metric['max']:.4f}]"

    for item in sorted(summary, key=lambda r: tuple(str(r[k]) for k in keys)):
        sizes = item["file_bytes"]
        lines.append(
            f"| {item['shape']} | {item['n']:,} | {item['target']:,} "
            f"| {item['method']} "
            f"| {item['batch'] or '—'} | {item['bytes']['median']:,} "
            f"| {item['files']['median']} | {duration(item, 'write_s')} "
            f"| {duration(item, 'read_s')} "
            f"| {sizes['min']:,} / {sizes['median']:,.0f} / {sizes['max']:,} "
            f"| {item['removed_empty_files']['median']} "
            f"| {item['preparation_added_null_keys']:,} |"
        )
    args.results.with_suffix(".tables.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    validation = dict(
        configurations=len(expected),
        trials=len(rows),
        records_checked_per_stage=sum(row["n"] for row in rows),
        all_correct=True,
        source_hashes_match=True,
        retained_empty_files=0,
        transient_empty_files_removed=sum(row["removed_empty_files"] for row in rows),
    )
    args.results.with_suffix(".validation.json").write_text(
        json.dumps(validation, indent=2), encoding="utf-8"
    )
    print(json.dumps(validation))


if __name__ == "__main__":
    main()
