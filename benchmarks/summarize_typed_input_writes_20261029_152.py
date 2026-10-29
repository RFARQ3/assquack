"""Verify the exact write matrix and render reviewable timing/fragment tables."""

from __future__ import annotations

import hashlib
import itertools
import json
import statistics
import sys
from pathlib import Path


def main():
    path = Path(sys.argv[1])
    metadata = json.loads(path.with_suffix(".metadata.json").read_text())
    fixtures = json.loads(
        path.with_suffix(".fixtures.json").read_text(encoding="utf-8")
    )
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    script = Path(__file__).with_name("typed_input_writes_20261029_152.py")
    assert metadata["duckdb"] == "1.5.2"
    assert metadata["script_sha256"] == hashlib.sha256(script.read_bytes()).hexdigest()
    expected = set(
        itertools.product(
            ["pandas", "table", "recordbatch"],
            ["smaller", "larger"],
            [1000] if metadata["smoke"] else [100000],
            [65536] if metadata["smoke"] else [65536, 262144, 1048576],
            ["copy", "direct"],
            range(3),
        )
    )
    keys = ("source", "shape", "n", "target", "method", "repetition")
    assert len(rows) == len(expected)
    assert {tuple(row[key] for key in keys) for row in rows} == expected
    for row in rows:
        assert row["correct"] and row["validated_records"] == row["n"]
        assert row["retained_empty_files"] == 0 and min(row["sizes"]) > 0
        assert len(row["sizes"]) == row["files"]
        assert sum(row["sizes"]) == row["bytes"]
        assert row["batch"] == (2048 if row["method"] == "direct" else None)
        if not metadata["smoke"]:
            assert row["bytes"] == fixtures[row["shape"]]["total_bytes"]

    lines = [
        "# Complete write results",
        "",
        "Seconds are median [minimum-maximum] over three repetitions. All rows use "
        + ("1,000" if metadata["smoke"] else "100,000")
        + " records.",
        "",
        "| Input | Shape | Target bytes | Python write s | COPY write s | Speedup |",
        "| --- | --- | ---: | --- | --- | ---: |",
    ]
    groups = sorted({(r["source"], r["shape"], r["target"]) for r in rows})
    for source, shape, target in groups:
        values = {}
        for method in ["direct", "copy"]:
            values[method] = [
                r["write_s"]
                for r in rows
                if (r["source"], r["shape"], r["target"], r["method"])
                == (source, shape, target, method)
            ]
        cells = [
            f"{statistics.median(v):.4f} [{min(v):.4f}-{max(v):.4f}]"
            for v in values.values()
        ]
        speedup = statistics.median(values["direct"]) / statistics.median(
            values["copy"]
        )
        lines.append(
            f"| {source} | {shape} | {target:,} | "
            f"{' | '.join(cells)} | {speedup:.2f}x |"
        )
    lines.extend(
        [
            "",
            "## Actual fragments",
            "",
            "File count and removed empties are per-trial ranges. "
            "Size statistics pool all three repetitions.",
            "",
            "| Input | Shape | Target | Path | Total bytes | Files | "
            "File bytes min / median / max | Empty removed |",
            "| --- | --- | ---: | --- | ---: | --- | --- | --- |",
        ]
    )
    for source, shape, target in groups:
        for method in ["direct", "copy"]:
            group = [
                r
                for r in rows
                if (r["source"], r["shape"], r["target"], r["method"])
                == (source, shape, target, method)
            ]
            sizes = [s for r in group for s in r["sizes"]]
            counts = [r["files"] for r in group]
            empty = [r["removed_empty_files"] for r in group]
            assert len({r["bytes"] for r in group}) == 1
            lines.append(
                f"| {source} | {shape} | {target:,} | {method} | "
                f"{group[0]['bytes']:,} | "
                f"{min(counts)}-{max(counts)} | {min(sizes):,} / "
                f"{statistics.median(sizes):,.0f} / {max(sizes):,} | "
                f"{min(empty)}-{max(empty)} |"
            )
    path.with_suffix(".tables.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    validation = dict(
        trials=len(rows),
        configurations=len(rows) // 3,
        records_checked=sum(r["validated_records"] for r in rows),
        retained_empty_files=0,
        removed_empty_files=sum(r["removed_empty_files"] for r in rows),
        version_gate=True,
        source_hash=True,
        full_matrix=True,
    )
    path.with_suffix(".validation.json").write_text(json.dumps(validation, indent=2))
    print(json.dumps(validation, indent=2))


if __name__ == "__main__":
    main()
