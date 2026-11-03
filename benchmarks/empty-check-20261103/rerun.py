"""One bounded sensitivity rerun of cases with wide original timing ranges."""

import ctypes
import hashlib
import json

import duckdb
import run


def main():
    records = [
        json.loads(line) for line in (run.HERE / "raw.jsonl").read_text().splitlines()
    ]
    summary = [
        {
            "case": case,
            "durations_ms": {
                s: [
                    r["total_seconds"] * 1000
                    for r in records
                    if r["case"] == case and r["strategy"] == s and r["repeat"] >= 0
                ]
                for s in ("A", "B")
            },
        }
        for case in dict.fromkeys(r["case"] for r in records)
    ]
    selected = [
        row["case"]
        for row in summary
        if any(
            max(v) / min(v) > 1.4 and max(v) - min(v) > 10
            for v in row["durations_ms"].values()
        )
    ]
    assert len(selected) == 5, selected
    path = run.HERE / "reruns.jsonl"
    assert not path.exists()
    (run.HERE / "rerun-selection.json").write_text(
        json.dumps(
            {
                "criterion": "max/min > 1.4 AND max-min > 10ms in either strategy",
                "reason": "Possible scheduling/filesystem variability; "
                "no confirmed sleep",
                "cases": selected,
                "bound": "one warmup plus three measured pairs per case",
                "capture_harness_sha256": hashlib.sha256(
                    (run.HERE / "run.py").read_bytes()
                ).hexdigest(),
            },
            indent=2,
        )
    )
    with duckdb.connect() as connection:
        run.configure(connection)
        for index, case in enumerate(selected):
            family, row_text, result = case.split("-")
            count = int(row_text)
            run.fixtures(connection, count)
            sql = run.query(family, result == "empty", count)
            expected = run.first_row(connection.sql(sql).count("*"))[0]
            before = run.catalog(connection)
            for repeat in range(-1, 3):
                order = ("A", "B") if (repeat + index) % 2 else ("B", "A")
                for strategy in order:
                    relation = connection.sql(sql)
                    directory = run.SCRATCH / "capture"
                    fragments, timing = run.capture(relation, directory, strategy)
                    run.validate(
                        connection, sql, directory, fragments, expected, before
                    )
                    assert run.first_row(relation.count("*"))[0] == expected
                    record = dict(
                        case=case,
                        strategy=strategy,
                        repeat=repeat,
                        order=order,
                        **timing,
                        validated=True,
                    )
                    with path.open("a", encoding="utf-8") as output:
                        output.write(json.dumps(record) + "\n")
                    run.remove_scratch(directory)
            print(case, flush=True)


if __name__ == "__main__":
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    try:
        main()
    finally:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
