"""Render measured capture trials without treating failures as successes."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent / "capture-results-20261102"


def audit(records) -> None:
    """Check that all scheduled trials retained comparable source contracts."""
    assert len(records) == 984, f"Expected 984 records, found {len(records)}"
    assert len({row["name"] for row in records}) == len(records)
    measured = [row for row in records if "version" in row]
    assert {row["version"] for row in measured} == {"1.5.2"}
    assert len({row["historical_sha256"] for row in measured}) == 1
    contracts = defaultdict(set)
    trials = defaultdict(list)
    for row in records:
        key = tuple(
            row[k] for k in ("shape", "rows", "plan", "profile", "path", "lens")
        )
        trials[key].append(row["trial"])
        if "source_settings" not in row:
            assert not row.get("validated") and row.get("error")
            continue
        source = row["source_settings"]
        assert source["threads"] == "4" and source["memory_limit"] == "7.4 GiB"
        assert source["TimeZone"] == "UTC"
        assert row["target"] == 64_000_000
        contract = json.dumps([row["sql"], row["types"], row["plan_text"]])
        contracts[(row["shape"], row["rows"], row["plan"])].add(contract)
        if row["path"] == "current" and row["profile"] == "controlled":
            for setting in ("threads", "memory_limit", "TimeZone"):
                assert row["bridge_settings"][setting] == source[setting]
        if row.get("validated"):
            assert sum(row["fragment_rows"]) == row["rows"]
            assert all(count > 0 for count in row["fragment_rows"])
            assert all(size > 0 for size in row["fragment_sizes"])
            if row["lens"] == "memory":
                assert row["samples"] > 0
                assert row["increment_bytes"] == [
                    peak - baseline
                    for peak, baseline in zip(
                        row["peak_bytes"], row["baseline_bytes"], strict=True
                    )
                ]
    assert len(contracts) == 16 and all(
        len(values) == 1 for values in contracts.values()
    )
    assert len(trials) == 192
    for key, values in trials.items():
        smallest = (key[0], key[1]) in {
            ("flat", 100_000),
            ("nested", 100_000),
            ("wide", 10_000),
        }
        assert sorted(values) == list(range(-1, 6 if smallest else 3)), key
    print(
        "Audit passed: complete attempted matrix; "
        "measured source SQL/types/plans and settings verified"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--complete", action="store_true")
    args = parser.parse_args()
    groups = defaultdict(list)
    records = []
    for path in sorted(DIRECTORY.glob("tier-*.jsonl")):
        records.extend(json.loads(line) for line in path.read_text().splitlines())
    if args.complete:
        audit(records)
    for row in records:
        if row.get("trial", -1) >= 0 and row.get("validated"):
            key = tuple(row[k] for k in ("shape", "rows", "plan", "profile", "path"))
            groups[key].append(row)

    gates = [
        json.loads(line)
        for line in (DIRECTORY / "gates.jsonl").read_text().splitlines()
    ]
    lines = [
        "# Fragment capture results, 2 November 2026",
        "",
        f"Status: {'complete' if len(records) == 984 else 'in progress'} "
        f"({len(records)}/984 scheduled performance records).",
        "",
        "Generated from retained JSONL trials; see "
        "[methodology](../capture-20261102-methodology.md).",
        "",
        f"Correctness gates: {sum(bool(r.get('validated')) for r in gates)}"
        f"/{len(gates)} passed.",
        f"Performance records (including warm-ups): {len(records)}; "
        f"failed: {sum(not r.get('validated', False) for r in records)}.",
        "",
        "Times use speed-only trials. Memory uses separate instrumented trials "
        "over the same interval.",
        "Memory cells show median baseline / sampled peak / increment in MiB. "
        "RSS is working set;",
        "private commitment is PrivateUsage. Sampling can miss transient peaks.",
        "",
        "| Shape / rows | Plan | Settings | Path | Trials speed/memory | "
        "Seconds median [min-max] | RSS MiB baseline/peak/increment | "
        "Private MiB baseline/peak/increment | Fragments min-max | Output MB min-max |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    summary = []
    for key, rows in sorted(groups.items()):
        speed = [r for r in rows if r["lens"] == "speed"]
        memory = [r for r in rows if r["lens"] == "memory"]
        times = [r["elapsed_seconds"] for r in speed]
        seconds = (
            f"{statistics.median(times):.4f} [{min(times):.4f}-{max(times):.4f}]"
            if times
            else "pending"
        )
        memory_cells = []
        for metric in range(2):
            if not memory:
                memory_cells.append("pending")
                continue
            values = [
                statistics.median(r[field][metric] for r in memory) / 2**20
                for field in ("baseline_bytes", "peak_bytes", "increment_bytes")
            ]
            memory_cells.append(" / ".join(f"{v:.1f}" for v in values))
        fragments = [len(r["fragment_sizes"]) for r in rows]
        sizes = [sum(r["fragment_sizes"]) / 1_000_000 for r in rows]
        shape, count, plan, profile, path = key
        lines.append(
            f"| {shape} / {count:,} | {plan} | {profile} | {path} | "
            f"{len(speed)}/{len(memory)} | {seconds} | "
            f"{memory_cells[0]} | {memory_cells[1]} | "
            f"{min(fragments)}-{max(fragments)} | {min(sizes):.2f}-{max(sizes):.2f} |"
        )
        summary.append(
            dict(zip(("shape", "rows", "plan", "profile", "path"), key, strict=True))
            | {
                "speed_trials": len(speed),
                "memory_trials": len(memory),
                "median_seconds": statistics.median(times) if times else None,
            }
        )
    all_memory = [r for r in records if r.get("validated") and r["lens"] == "memory"]
    if all_memory:
        lines += [
            "",
            "Maximum observed sampling gap: "
            f"{max(r['max_sample_gap_seconds'] for r in all_memory) * 1000:.1f} ms.",
        ]
    failures = [r for r in [*gates, *records] if not r.get("validated")]
    if failures:
        lines += ["", "## Failed cases", ""]
        lines.extend(
            f"- `{r['name']}`: {r.get('error', 'no validated output').splitlines()[-1]}"
            for r in failures
        )
    (DIRECTORY / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (DIRECTORY / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(f"Summarized {len(records)} performance records, {len(groups)} groups")


if __name__ == "__main__":
    main()
