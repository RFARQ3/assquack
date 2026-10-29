"""Summarize one dict_fragments_20261028.py JSONL run without rerunning it."""

from __future__ import annotations

import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def main() -> None:
    path = Path(sys.argv[1])
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    groups = defaultdict(list)
    keys = ["shape", "n", "source", "target", "method", "batch"]
    for row in rows:
        assert row["correct"]
        groups[tuple(row[k] for k in keys)].append(row)
    summary = []
    for key, trials in groups.items():
        assert len(trials) == 3, (key, len(trials))
        entry = dict(zip(keys, key, strict=True))
        for metric in [
            "write_s",
            "inference_s",
            "read_s",
            "write_baseline_rss",
            "write_peak_rss",
            "read_baseline_rss",
            "read_peak_rss",
            "bytes",
            "files",
            "max_overshoot",
            "under_half_target",
        ]:
            values = [row[metric] for row in trials]
            entry[metric] = statistics.median(values)
            if metric.endswith("_s"):
                entry[metric + "_min"] = min(values)
                entry[metric + "_max"] = max(values)
        entry["combined_s"] = statistics.median(
            r["write_s"] + r["read_s"] for r in trials
        )
        entry["write_increment_rss"] = statistics.median(
            r["write_peak_rss"] - r["write_baseline_rss"] for r in trials
        )
        sizes = [size for row in trials for size in row["sizes"]]
        entry["file_min"] = min(sizes)
        entry["file_median"] = statistics.median(sizes)
        entry["file_max"] = max(sizes)
        entry["empty_files"] = statistics.median(
            sum(size == 0 for size in row["sizes"]) for row in trials
        )
        entry["normalized_records"] = statistics.median(
            r["normalized_records"] for r in trials
        )
        summary.append(entry)
    summary.sort(key=lambda x: tuple(str(x[k]) for k in keys))
    with path.with_suffix(".summary.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    path.with_suffix(".summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    by_workload = defaultdict(list)
    for row in summary:
        by_workload[(row["shape"], row["n"], row["source"], row["target"])].append(row)
    print("Fastest median writing and combined writing/read per workload:")
    wins = defaultdict(int)
    combined_wins = defaultdict(int)
    for key, candidates in sorted(by_workload.items()):
        winner = min(candidates, key=lambda r: r["write_s"])
        combined = min(candidates, key=lambda r: r["combined_s"])
        wins[winner["method"]] += 1
        combined_wins[combined["method"]] += 1
        print(
            key,
            winner["method"],
            winner["batch"],
            round(winner["write_s"], 4),
            "combined:",
            combined["method"],
            combined["batch"],
            round(combined["combined_s"], 4),
        )
    print("WINS", dict(wins), "COMBINED", dict(combined_wins))
    print("256 KiB LIST cases, fastest batch per method:")
    for key, candidates in sorted(by_workload.items()):
        if key[2:] != ("list", 262144):
            continue
        for method in ["jsonlines", "direct", "duck_batch", "duck_all"]:
            r = min(
                (r for r in candidates if r["method"] == method),
                key=lambda r: r["write_s"],
            )
            print(
                key[:2],
                method,
                r["batch"],
                f"write={r['write_s']:.4f} read={r['read_s']:.4f} "
                f"infer={r['inference_s']:.4f}",
                f"rss={r['write_peak_rss'] / 1048576:.1f} "
                f"delta={r['write_increment_rss'] / 1048576:.1f}",
                f"files={r['files']} "
                f"size={r['file_min']}/{r['file_median']}/{r['file_max']}",
            )


if __name__ == "__main__":
    main()
