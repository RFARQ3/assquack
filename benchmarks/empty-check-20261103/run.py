"""Isolated, deterministic comparison of probe-then-COPY and direct COPY."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import shutil
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
SCRATCH = HERE / "scratch"
TARGET_BYTES = 64_000_000
FAMILIES = ("scan", "filter", "join", "aggregate", "window")


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def first_row(result):
    row = result.fetchone()
    assert row is not None
    return row


@dataclass
class Fragment:
    index: int
    path: str
    row_count: int


def capture(relation: duckdb.DuckDBPyRelation, directory: Path, strategy: str):
    """Use the historical direct COPY mechanics with one optional first-row probe."""
    started = time.perf_counter()
    cpu_started = time.process_time()
    directory.mkdir(parents=True, exist_ok=False)
    probe_seconds = 0.0
    should_copy = True
    if strategy == "A":
        probe_started = time.perf_counter()
        should_copy = relation.limit(1).fetchone() is not None
        probe_seconds = time.perf_counter() - probe_started

    # The local Python variable is resolved by DuckDB's replacement scan. There
    # is no catalog registration, intermediate format, or eager fetch of all rows.
    if should_copy:
        duckdb.execute(
            f"COPY relation TO {literal(str(directory))} "
            f"(FORMAT JSON, ARRAY false, FILE_SIZE_BYTES {TARGET_BYTES}, "
            "FILE_EXTENSION partial, OVERWRITE_OR_IGNORE true, "
            "FILENAME_PATTERN 'copy_000000_{i}')"
        )

    fragments = []
    zero_files = 0
    for partial in sorted(directory.glob("copy_000000_*.partial")):
        with partial.open("rb") as source:
            count = sum(
                block.count(b"\n")
                for block in iter(lambda: source.read(1024 * 1024), b"")
            )
        if count == 0:
            partial.unlink()
            zero_files += 1
            continue
        destination = directory / f"fragment={len(fragments):06d}.ndjson"
        partial.replace(destination)
        fragments.append(Fragment(len(fragments), str(destination), count))
    (directory / "_COMPLETE").touch(exist_ok=False)
    cpu_seconds = time.process_time() - cpu_started
    elapsed = time.perf_counter() - started
    return fragments, {
        "total_seconds": elapsed,
        "cpu_seconds": cpu_seconds,
        "probe_seconds": probe_seconds,
        "copy_executed": should_copy,
        "zero_files_removed": zero_files,
    }


def query(family: str, empty: bool, rows: int) -> str:
    fields = "s.id, s.label, s.amount, s.active, s.detail"
    if family == "scan":
        # A substring absent from stored strings forces a scan, not a statistics
        # shortcut. The nonempty case is a plain full projection.
        predicate = " WHERE contains(s.label, 'missing')" if empty else ""
        return f"SELECT {fields} FROM source s{predicate}"
    if family == "filter":
        suffix = "zz" if empty else "00"
        return (
            f"SELECT {fields} FROM source s WHERE right(md5(s.label), 2) = '{suffix}'"
        )
    if family == "join":
        # Apply the empty predicate to joined values: neither side is statically
        # empty, and the equality hash join remains in the optimized plan.
        predicate = " WHERE contains(s.label || d.tag, 'missing')" if empty else ""
        return (
            f"SELECT {fields}, d.tag FROM source s JOIN dimension d "
            f"ON s.id % 1000 = d.key{predicate}"
        )
    if family == "aggregate":
        threshold = "< 0" if empty else "> 0"
        return (
            "SELECT id % 1000 AS bucket, count(*) AS records, "
            "sum(amount) AS total, min(label) AS first_label FROM source "
            f"GROUP BY id % 1000 HAVING sum(amount) {threshold}"
        )
    threshold = f"> {rows}" if empty else f"<= {rows // 10}"
    return (
        f"SELECT {fields}, row_number() OVER (ORDER BY s.label, s.id) AS rn "
        f"FROM source s QUALIFY rn {threshold} ORDER BY rn"
    )


def catalog(connection):
    return connection.execute(
        "SELECT schema_name, view_name, sql FROM duckdb_views() ORDER BY 1, 2"
    ).fetchall()


def validate(connection, sql, directory, fragments, expected_count, before_views):
    files = sorted(directory.glob("*.ndjson"))
    assert set(directory.iterdir()) == {*files, directory / "_COMPLETE"}
    assert {str(path) for path in files} == {f.path for f in fragments}
    assert sum(f.row_count for f in fragments) == expected_count
    assert all(f.row_count > 0 for f in fragments)
    assert catalog(connection) == before_views
    assert not list(SCRATCH.rglob("*.parquet"))
    if not expected_count:
        assert not fragments
        return

    # Exact multiset comparison of serialized JSON, in both directions, checks
    # nested values and duplicate multiplicities without Python materialization.
    paths = ",".join(literal(str(path)) for path in files)
    difference = connection.execute(
        f"WITH expected AS (SELECT to_json(t)::VARCHAR AS j FROM ({sql}) t), "
        f"actual AS (SELECT json::VARCHAR AS j FROM read_json_objects([{paths}], "
        "format='newline_delimited')) "
        "SELECT (SELECT count(*) FROM (SELECT * FROM expected EXCEPT ALL "
        "SELECT * FROM actual)) + (SELECT count(*) FROM (SELECT * FROM actual "
        "EXCEPT ALL SELECT * FROM expected))"
    ).fetchone()
    assert difference == (0,), difference


def remove_scratch(path: Path):
    assert path.resolve().is_relative_to(SCRATCH.resolve())
    shutil.rmtree(path)


def configure(connection):
    connection.execute("SET threads = 4")
    connection.execute("SET memory_limit = '4GB'")
    connection.execute("SET TimeZone = 'UTC'")
    connection.execute("SET temp_directory = ?", [str(SCRATCH / "spill")])
    duckdb.set_default_connection(connection)


def fixtures(connection, rows):
    connection.execute(
        "CREATE OR REPLACE TABLE source AS SELECT i AS id, md5(i::VARCHAR) AS label, "
        "(i % 10000)::DOUBLE / 4 + 1 AS amount, i % 2 = 0 AS active, "
        "struct_pack(note := 'café 東京', items := [i, NULL, i + 1]) AS detail "
        "FROM range(?) t(i)",
        [rows],
    )
    connection.execute(
        "CREATE OR REPLACE TABLE dimension AS SELECT i AS key, "
        "'tag-' || i::VARCHAR AS tag FROM range(1000) t(i)"
    )


def gates(connection):
    """Prove reuse and repeated source execution outside the timed matrix."""
    evidence = {}
    for strategy in ("A", "B"):
        connection.execute("CREATE OR REPLACE SEQUENCE gate_sequence START 1")
        relation = connection.sql(
            "SELECT nextval('gate_sequence') AS id FROM range(10)"
        )
        directory = SCRATCH / "gate"
        fragments, timings = capture(relation, directory, strategy)
        count_after_capture = connection.execute(
            "SELECT currval('gate_sequence')"
        ).fetchone()[0]
        assert sum(f.row_count for f in fragments) == 10
        assert relation.fetchall() == [
            (i,) for i in range(count_after_capture + 1, count_after_capture + 11)
        ]
        evidence[strategy] = dict(
            calls_after_capture=count_after_capture,
            calls_after_relation_reuse=connection.execute(
                "SELECT currval('gate_sequence')"
            ).fetchone()[0],
            **timings,
        )
        assert count_after_capture == (11 if strategy == "A" else 10)
        remove_scratch(directory)
    connection.execute("DROP SEQUENCE gate_sequence")
    (HERE / "gates.json").write_text(json.dumps(evidence, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gates-only", action="store_true")
    args = parser.parse_args()
    assert duckdb.__version__ == "1.5.2"
    SCRATCH.mkdir(exist_ok=True)
    with duckdb.connect() as connection:
        configure(connection)
        gates(connection)
        if args.gates_only:
            fixtures(connection, 10000)
            for family in FAMILIES:
                for empty in (True, False):
                    sql = query(family, empty, 10000)
                    expected = first_row(connection.sql(sql).count("*"))[0]
                    for strategy in ("A", "B"):
                        before = catalog(connection)
                        fragments, _ = capture(
                            connection.sql(sql), SCRATCH / "gate", strategy
                        )
                        validate(
                            connection,
                            sql,
                            SCRATCH / "gate",
                            fragments,
                            expected,
                            before,
                        )
                        remove_scratch(SCRATCH / "gate")
            print("All correctness gates passed", flush=True)
            return

        destination = HERE / "raw.jsonl"
        if destination.exists():
            raise RuntimeError("Refusing to overwrite raw timings")
        environment = {
            "python": sys.version,
            "interpreter": sys.executable,
            "duckdb": duckdb.__version__,
            "platform": platform.platform(),
            "processor": platform.processor(),
            "logical_cpus": os.cpu_count(),
            "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "reference_sha256": hashlib.sha256(
                (HERE.parent / "capture_20261102.py").read_bytes()
            ).hexdigest(),
            "historical_revision": "5ab0e2192422b3bce3df58fd860d5c6dd4ba5007",
            "settings": connection.execute(
                "SELECT name,value FROM duckdb_settings() WHERE name IN "
                "('threads','memory_limit','TimeZone','preserve_insertion_order',"
                "'temp_directory')"
            ).fetchall(),
            "target_bytes": TARGET_BYTES,
            "utc_started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        (HERE / "environment.json").write_text(json.dumps(environment, indent=2))
        plans = HERE / "plans"
        plans.mkdir(exist_ok=True)
        records = []
        for rows in (10_000, 100_000, 1_000_000):
            fixtures(connection, rows)
            for family_index, family in enumerate(FAMILIES):
                for empty in (True, False):
                    case = f"{family}-{rows}-{'empty' if empty else 'nonempty'}"
                    sql = query(family, empty, rows)
                    expected = first_row(connection.sql(sql).count("*"))[0]
                    assert (expected == 0) == empty
                    relation = connection.sql(sql)
                    (plans / f"{case}.txt").write_text(
                        f"SQL: {sql}\nInput: {rows}; dimension: 1000; "
                        f"output: {expected}\n"
                        + "FULL PLAN\n"
                        + relation.explain()
                        + "\nPROBE PLAN\n"
                        + relation.limit(1).explain()
                        + "\nFULL EXECUTION\n"
                        + first_row(connection.execute("EXPLAIN ANALYZE " + sql))[1]
                        + "\nPROBE EXECUTION\n"
                        + first_row(
                            connection.execute(
                                f"EXPLAIN ANALYZE SELECT * FROM ({sql}) LIMIT 1"
                            )
                        )[1],
                        encoding="utf-8",
                    )
                    before = catalog(connection)
                    for repeat in range(-1, 3):
                        # Across the matrix each strategy leads exactly half the
                        # measured pairs; within a case order alternates.
                        order = (
                            ("A", "B")
                            if (repeat + family_index + int(empty)) % 2
                            else ("B", "A")
                        )
                        for strategy in order:
                            relation = connection.sql(sql)
                            directory = SCRATCH / "capture"
                            fragments, timing = capture(relation, directory, strategy)
                            validate(
                                connection, sql, directory, fragments, expected, before
                            )
                            assert first_row(relation.count("*"))[0] == expected
                            record = dict(
                                case=case,
                                family=family,
                                input_rows=rows,
                                output_rows=expected,
                                empty=empty,
                                strategy=strategy,
                                repeat=repeat,
                                order=list(order),
                                **timing,
                                fragments=[asdict(f) for f in fragments],
                                validated=True,
                            )
                            with destination.open("a", encoding="utf-8") as output:
                                output.write(json.dumps(record) + "\n")
                            records.append(record)
                            remove_scratch(directory)
                    measured = [
                        r for r in records if r["case"] == case and r["repeat"] >= 0
                    ]
                    medians = {
                        s: statistics.median(
                            r["total_seconds"] for r in measured if r["strategy"] == s
                        )
                        for s in ("A", "B")
                    }
                    print(
                        json.dumps(
                            dict(case=case, output_rows=expected, medians=medians)
                        ),
                        flush=True,
                    )
        assert len(records) == 240
        print("Completed 240 validated trials", flush=True)


if __name__ == "__main__":
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    try:
        main()
    finally:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
