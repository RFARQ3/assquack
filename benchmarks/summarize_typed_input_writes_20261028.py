"""Validate a complete run and produce write-only tables without importing writers."""

from __future__ import annotations

import csv
import hashlib
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

KEYS = ("source", "shape", "n", "target", "method", "batch")


def main():
    path = Path(sys.argv[1])
    metadata = json.loads(path.with_suffix(".metadata.json").read_text())
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    script = Path(__file__).with_name("typed_input_writes_20261028.py")
    assert hashlib.sha256(script.read_bytes()).hexdigest() == metadata["script_sha256"]
    expected = {tuple(c[k] for k in KEYS) for c in metadata["configurations"]}
    groups = defaultdict(list)
    for row in rows:
        assert row["correct"] and row["validated_records"] == row["n"]
        assert row["retained_empty_files"] == 0 and min(row["sizes"]) > 0
        assert sum(row["sizes"]) == row["bytes"]
        assert len(row["sizes"]) == row["files"]
        groups[tuple(row[k] for k in KEYS)].append(row)
    assert set(groups) == expected
    table = []
    for key, trials in groups.items():
        assert len(trials) == 3 and {r["repetition"] for r in trials} == {0, 1, 2}
        assert len({tuple(r["sizes"]) for r in trials}) == 1
        durations = [r["write_s"] for r in trials]
        sizes = trials[0]["sizes"]
        item: dict[str, Any] = dict(zip(KEYS, key, strict=True))
        item.update(
            write_median_s=statistics.median(durations),
            write_min_s=min(durations),
            write_max_s=max(durations),
            actual_bytes=trials[0]["bytes"],
            files=len(sizes),
            file_min_bytes=min(sizes),
            file_median_bytes=statistics.median(sizes),
            file_max_bytes=max(sizes),
            removed_empty_files=trials[0]["removed_empty_files"],
        )
        table.append(item)
    table.sort(key=lambda r: tuple(str(r[k]) for k in KEYS))
    with path.with_suffix(".summary.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(table[0]))
        writer.writeheader()
        writer.writerows(table)
    winners = defaultdict(lambda: defaultdict(int))
    cases = defaultdict(list)
    for row in table:
        cases[tuple(row[k] for k in KEYS[:4])].append(row)
    for candidates in cases.values():
        best = min(candidates, key=lambda r: r["write_median_s"])
        winners[best["source"]][best["method"]] += 1
    validation = dict(
        trials=len(rows),
        configurations=len(groups),
        records_validated=sum(r["n"] for r in rows),
        retained_empty_files=0,
        removed_empty_files=sum(r["removed_empty_files"] for r in rows),
        lowest_write_median_counts=dict(winners),
        source_hash_matches=True,
        full_matrix=True,
    )
    path.with_suffix(".validation.json").write_text(json.dumps(validation, indent=2))
    print(json.dumps(validation, indent=2))
    selected = [
        r
        for r in table
        if r["target"] == 262144
        and r["n"] == (100000 if r["shape"] == "narrow" else 10000)
        and r["batch"] in (None, 2048)
    ]
    lines = [
        "| Source | Shape / rows | Writer | Write seconds median [min-max] "
        "| Bytes | Files | File bytes min / median / max | Empty removals |",
        "| --- | --- | --- | --- | ---: | ---: | --- | ---: |",
    ]
    for r in selected:
        lines.append(
            f"| {r['source']} | {r['shape']} / {r['n']:,} | {r['method']} | "
            f"{r['write_median_s']:.4f} "
            f"[{r['write_min_s']:.4f}-{r['write_max_s']:.4f}] | "
            f"{r['actual_bytes']:,} | {r['files']} | "
            f"{r['file_min_bytes']:,} / {r['file_median_bytes']:,} / "
            f"{r['file_max_bytes']:,} | "
            f"{r['removed_empty_files']} |"
        )
    path.with_suffix(".table.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
