"""Summarize the retained capture timings without importing the harness."""

import json
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    records = [
        json.loads(line) for line in (HERE / "raw.jsonl").read_text().splitlines()
    ]
    assert len(records) == 240 and all(row["validated"] for row in records)
    cases = list(dict.fromkeys(row["case"] for row in records))
    lines = [
        "# Empty-probe benchmark — 3 November 2026",
        "",
        "A = `limit(1).fetchone()` then COPY only if nonempty. "
        "B = direct COPY, deleting zero-record output. "
        "Both use identical fragment counting, registration and completion markers.",
        "",
        "**Neither wins universally.** A saves 21-57% on all five 10k-row empty "
        "workloads. B wins the million-row empty scan (6.63 vs 13.11 ms) and "
        "empty selective filter (74.49 vs 228.78 ms): 49% and 67% less elapsed "
        "time respectively. For nonempty grouped aggregation and window queries, "
        "A costs 39-99% more in the initial matrix; bounded reruns confirm B's "
        "advantage. Nonempty scans/filters/joins generally have overlapping "
        "ranges, so their smaller median differences do not establish a winner.",
        "",
        "Median total capture milliseconds (three measured repeats). "
        "Delta = (A/B - 1) * 100: positive favours B; negative favours A. "
        "Probe is part of A's total. An asterisk marks overlapping observed "
        "A/B ranges; it is not a significance test. Ranges are in summary.json.",
        "",
        "| Query | Input | Output | A ms | B ms | Delta A vs B | A probe ms |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    summary = []
    for case in cases:
        rows = [r for r in records if r["case"] == case and r["repeat"] >= 0]
        durations = {
            s: [r["total_seconds"] * 1000 for r in rows if r["strategy"] == s]
            for s in ("A", "B")
        }
        medians = {s: statistics.median(v) for s, v in durations.items()}
        probe = statistics.median(
            r["probe_seconds"] * 1000 for r in rows if r["strategy"] == "A"
        )
        delta = (medians["A"] / medians["B"] - 1) * 100
        first = rows[0]
        summary.append(
            dict(
                case=case,
                medians_ms=medians,
                durations_ms=durations,
                delta_percent=delta,
                probe_ms=probe,
                ranges_overlap=max(min(v) for v in durations.values())
                <= min(max(v) for v in durations.values()),
            )
        )
        overlap = "*" if summary[-1]["ranges_overlap"] else ""
        lines.append(
            f"| {first['family']} | {first['input_rows']:,} | "
            f"{first['output_rows']:,} | {medians['A']:.2f} | {medians['B']:.2f} | "
            f"{delta:+.1f}%{overlap} | {probe:.2f} |"
        )
    reruns = [
        json.loads(line) for line in (HERE / "reruns.jsonl").read_text().splitlines()
    ]
    assert len(reruns) == 40 and all(r["validated"] for r in reruns)
    lines += [
        "",
        "Five variable cases received one bounded rerun (one warmup, three "
        "measured pairs). Selection: either strategy's max/min > 1.4 and "
        "max-min > 10 ms. Original observations above are retained, not replaced. "
        "No sleep was observed; scheduling/filesystem effects are possible. "
        "The large absolute shifts show why fine percentage claims are unsafe.",
        "",
        "| Repeated case | A ms | B ms | Delta A vs B |",
        "|---|---:|---:|---:|",
    ]
    for case in dict.fromkeys(r["case"] for r in reruns):
        medians = {
            s: statistics.median(
                r["total_seconds"] * 1000
                for r in reruns
                if r["case"] == case and r["strategy"] == s and r["repeat"] >= 0
            )
            for s in ("A", "B")
        }
        delta = (medians["A"] / medians["B"] - 1) * 100
        lines.append(
            f"| {case} | {medians['A']:.2f} | {medians['B']:.2f} | {delta:+.1f}% |"
        )
    lines += [
        "",
        "The 100k empty window reverses its initial winner; treat it as unstable. "
        "The nonempty 100k join's apparent A advantage shrinks to about 2%, "
        "with overlapping ranges. Empty joins favour A by median at all sizes, "
        "but million-row ranges overlap. Empty aggregation favours A by median "
        "(43%, 10%, 8% less time), with overlap at 100k and 1m. The million-row "
        "empty window is effectively tied (1% median difference).",
        "",
        "Plans confirm actual HASH_JOIN, HASH_GROUP_BY and WINDOW operators; "
        "empty cases are not optimized to EMPTY_RESULT. A's window probe "
        "replaces the final ORDER_BY with TOP_N, but still computes the upstream "
        "window across all input. Successful scan/filter probes can stop early; "
        "aggregate/window probes must do substantial upstream work before COPY "
        "does it again. For the million-row empty selective filter, EXPLAIN "
        "ANALYZE shows full-query wall/scan-operator times of 72/280 ms versus "
        "229/230 ms with STREAMING_LIMIT. This is consistent with reduced "
        "parallelism in the probe, not an optimized-away empty predicate.",
        "",
        "All 280 trials passed exact JSON multiset/row equivalence, fragment and "
        "marker checks, no residual views/Parquet, and relation reuse. B removed "
        "its zero-record files; A skipped them. A separate sequence gate proved "
        "11 sequence calls for 10 rows under A versus 10 under B. Volatile "
        "sources can therefore change values or emptiness between executions. "
        "Timed fixtures were deterministic.",
        "",
        "This is a warmed, local synthetic benchmark: DuckDB 1.5.2, Python "
        "3.13.9, Windows 11, 8 logical CPUs, four DuckDB threads, 4 GB memory "
        "limit, 64 MB fragment target. Timings include probe/COPY/finalization "
        "and exclude fixture creation and correctness checks. Doer and reviewer "
        "confirmed their full validations had finished before timing; no heavy "
        "collaborator work overlapped. Three repeats cannot establish small "
        "differences or generalize to remote sources/cold disks. No production "
        "code or configuration was changed and no implementation was selected.",
        "",
        "Verification: correctness gates, full matrix and bounded reruns; Ruff "
        "lint/format; isolated Pyright; whitespace check. See "
        "[method and reproduction](README.md), [raw trials](raw.jsonl), "
        "[reruns](reruns.jsonl), [plans](plans/), [environment](environment.json).",
    ]
    (HERE / "summary.json").write_text(json.dumps(summary, indent=2))
    (HERE / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
