"""Offline NDJSON writer/sizing diagnostics; run with the project's Python."""
# Imports follow the explicit isolated benchmark dependency path.
# ruff: noqa: E402

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / ".tmp-normalization-benchmark/deps"), str(ROOT / "src")]

import duckdb
import orjson
import psutil
import pyarrow as pa

from assquack._materialization.json_reader import JSON_READER_OPTIONS
from assquack._materialization.models import FragmentFile
from assquack._materialization.schema import CurrentRunSchemaStrategy
from assquack._materialization.staging import create_shaped_staging
from assquack._storage.tables import TableReference

SCRATCH = ROOT / ".tmp-ndjson-diagnostics"
RESULTS = ROOT / "benchmarks/results/ndjson-2026-10-28.jsonl"
BATCH = 1024
TARGETS = [1_000_000, 10_000_000, 50_000_000]


def record(i, n, shape):
    row = {
        "id": i,
        "label": "row-" + str(i) + "x" * 100,
        "value": i / 4,
        "optional": None if i % 7 == 0 else i,
        "active": i % 2 == 0,
        "late": i if i >= n * 9 // 10 else None,
    }
    if shape == "nested":
        row.update(
            details={"text": 'café 東京\\"\n' * (100 + i % 200), "flag": i % 3 == 0},
            items=[i, i + 1, None],
            empty=[],
        )
    return row


def fixture(n, shape):
    # Explicit schema keeps the late field when constructing bounded Arrow batches.
    schema = pa.Table.from_pylist([record(n - 1, n, shape)]).schema
    if shape == "nested":
        schema = schema.set(
            schema.get_field_index("empty"), pa.field("empty", pa.list_(pa.int64()))
        )
    batches = [
        pa.RecordBatch.from_pylist(
            [record(i, n, shape) for i in range(start, min(start + BATCH, n))],
            schema=schema,
        )
        for start in range(0, n, BATCH)
    ]
    return pa.Table.from_batches(batches)


def probe():
    SCRATCH.mkdir(exist_ok=True)
    con = duckdb.connect(config={"threads": 1})
    result = {}
    for option in [
        "FILE_SIZE_BYTES 1000000",
        "ROW_GROUP_SIZE 1024",
        "ROW_GROUPS_PER_FILE 1",
    ]:
        path = SCRATCH / ("probe-" + option.split()[0])
        assert not path.exists(), "Inspect prior probe output before rerunning"
        try:
            con.execute(
                f"COPY (SELECT 1 AS id) TO '{path.as_posix()}' "
                f"(FORMAT JSON, ARRAY false, {option})"
            )
            result[option] = "supported"
        except duckdb.Error as error:
            result[option] = str(error)
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            for child in path.iterdir():
                child.unlink()
            path.rmdir()
    con.close()
    return result


def compatibility_probes():
    """Expose representation/encoder differences separately from timed rankings."""
    rows = [
        {"id": 0, "optional": None, "details": {"first": 1}},
        {
            "id": 1,
            "optional": 2,
            "details": {"first": 2, "later": "nested"},
            "late": "present",
        },
    ]
    results = {}
    con = duckdb.connect(config={"threads": 1})
    for writer in ["json", "orjson", "duckdb", "copy"]:
        directory = SCRATCH / ("compatibility-" + writer)
        assert not directory.exists()
        write(
            rows,
            "dict",
            writer,
            "native" if writer == "copy" else "record",
            1_000_000,
            1000,
            directory,
            con,
        )
        decoded = [
            orjson.loads(line)
            for path in directory.glob("*.ndjson")
            for line in path.read_bytes().splitlines()
        ]
        decoded.sort(key=lambda row: row["id"])
        fragments = [
            FragmentFile(index=i, path=str(p), row_count=0)
            for i, p in enumerate(directory.glob("*.ndjson"))
        ]
        reader = duckdb.connect(config={"threads": 1})
        projections = CurrentRunSchemaStrategy(
            reader, lambda fragments=fragments: fragments
        ).resolve()
        create_shaped_staging(
            reader, TableReference("main", "candidate"), fragments, projections, "edge"
        )
        results[writer] = dict(
            decoded=decoded,
            late_field_preserved=decoded[1].get("late") == "present",
            types={p.column_name: p.duckdb_type for p in projections},
        )
        reader.close()
        for path in directory.iterdir():
            path.unlink()
        directory.rmdir()
    results["non_finite_encoding"] = {
        "json": json.dumps({"value": float("nan")}),
        "orjson": orjson.dumps({"value": float("nan")}).decode(),
    }
    con.close()
    (RESULTS.parent / "ndjson-2026-10-28-compatibility.json").write_text(
        json.dumps(results, indent=2)
    )
    print(json.dumps(results, indent=2))


def python_records(source, kind):
    if kind == "dict":
        for start in range(0, len(source), BATCH):
            yield source[start : start + BATCH]
    elif kind == "arrow":
        for batch in source.to_batches(max_chunksize=BATCH):
            yield batch.to_pylist()
    else:
        names = source.columns
        cursor = source.execute()
        while rows := cursor.fetchmany(BATCH):
            yield [dict(zip(names, row, strict=True)) for row in rows]


def write(source, kind, writer, sizing, target, row_limit, directory, con):
    if writer in {"duckdb", "copy"}:
        if kind == "relation":
            relation = source
        else:
            table = pa.Table.from_pylist(source) if kind == "dict" else source
            relation = con.from_arrow(table)
        relation.create_view("writer_input", replace=True)
    if writer == "copy":
        con.execute(
            f"COPY writer_input TO '{directory.as_posix()}' "
            f"(FORMAT JSON, ARRAY false, FILE_SIZE_BYTES {target}, "
            "FILENAME_PATTERN 'part_{i}')"
        )
        for path in directory.glob("*.json"):
            with path.open("rb+") as handle:
                os.fsync(handle.fileno())
            path.replace(path.with_suffix(".ndjson"))
        (directory / "_COMPLETE").touch()
        return

    directory.mkdir()
    if writer == "duckdb":
        cursor = con.execute("SELECT to_json(t) FROM writer_input t")

        def duckdb_batches():
            while rows := cursor.fetchmany(BATCH):
                yield [(row[0] + "\n").encode() for row in rows]

        encoded_batches = duckdb_batches
    else:

        def encoder_batches():
            for rows in python_records(source, kind):
                if writer == "orjson":
                    yield [
                        orjson.dumps(row, option=orjson.OPT_APPEND_NEWLINE)
                        for row in rows
                    ]

                else:
                    yield [
                        (
                            json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                            + "\n"
                        ).encode()
                        for row in rows
                    ]

        encoded_batches = encoder_batches

    handle = None
    size = count = index = 0

    def finish():
        assert handle is not None
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        partial.replace(directory / f"part_{index}.ndjson")

    for batch in encoded_batches():
        chunks = [batch] if sizing == "batch" else ([line] for line in batch)
        for chunk in chunks:
            if handle is None:
                partial = directory / f"part_{index}.partial"
                handle = partial.open("xb")
            data = b"".join(chunk)
            handle.write(data)
            size += len(data)
            count += len(chunk)
            if count >= row_limit if sizing == "rows" else size >= target:
                finish()
                handle = None
                index += 1
                size = count = 0
    if handle is not None:
        finish()
    (directory / "_COMPLETE").touch()


def verify(directory, connection, n, shape):
    # Compare every decoded fragment value and every typed candidate value.
    seen = bytearray(n)
    file_rows = []
    for path in sorted(directory.glob("*.ndjson")):
        count = 0
        with path.open("rb") as handle:
            for line in handle:
                assert line.endswith(b"\n"), "truncated record"
                value = orjson.loads(line)
                i = value["id"]
                assert not seen[i] and value == record(i, n, shape), (i, value)
                seen[i] = 1
                count += 1
        file_rows.append(count)
    assert sum(seen) == n
    columns = [
        row[0]
        for row in connection.execute("DESCRIBE candidate").fetchall()
        if not row[0].startswith("_qa_")
    ]
    cursor = connection.execute(
        "SELECT "
        + ",".join('"' + c + '"' for c in columns)
        + " FROM candidate ORDER BY id"
    )
    count = 0
    while rows := cursor.fetchmany(BATCH):
        for row in rows:
            assert dict(zip(columns, row, strict=True)) == record(count, n, shape), (
                count
            )
            count += 1
    assert count == n
    return file_rows


def trial(source, kind, n, shape, writer, sizing, target, row_limit, repetition, con):
    directory = SCRATCH / "current-output"
    assert not directory.exists(), "Prior output exists; inspect before removing"
    gc.collect()
    process = psutil.Process()
    baseline = process.memory_info().rss
    peak = [baseline]
    stop = threading.Event()

    def monitor():
        while not stop.wait(0.005):
            peak[0] = max(peak[0], process.memory_info().rss)

    thread = threading.Thread(target=monitor)
    thread.start()
    start = time.perf_counter()
    write(source, kind, writer, sizing, target, row_limit, directory, con)
    write_seconds = time.perf_counter() - start
    write_peak = peak[0]
    files = sorted(directory.glob("*.ndjson"))
    fragments = [
        FragmentFile(index=i, path=str(p), row_count=0) for i, p in enumerate(files)
    ]
    read_baseline = process.memory_info().rss
    peak[0] = read_baseline
    start = time.perf_counter()
    reader = duckdb.connect(config={"threads": 1})
    projections = CurrentRunSchemaStrategy(reader, lambda: fragments).resolve()
    create_shaped_staging(
        reader, TableReference("main", "candidate"), fragments, projections, "benchmark"
    )
    read_seconds = time.perf_counter() - start
    read_peak = peak[0]
    stop.set()
    thread.join()
    sizes = [p.stat().st_size for p in files]
    counts = verify(directory, reader, n, shape)
    types = {p.column_name: p.duckdb_type for p in projections}
    expected = {
        "active": "BOOLEAN",
        "id": "BIGINT",
        "label": "VARCHAR",
        "late": "BIGINT",
        "optional": "BIGINT",
        "value": "DOUBLE",
    }
    if shape == "nested":
        expected.update(
            details='STRUCT("text" VARCHAR, flag BOOLEAN)',
            items="BIGINT[]",
            empty="JSON[]",
        )
    assert types == expected, types
    reader.close()
    result = dict(
        kind=kind,
        rows=n,
        shape=shape,
        writer=writer,
        sizing=sizing,
        target_bytes=target if sizing != "rows" else None,
        row_limit=row_limit if sizing == "rows" else None,
        calibration_target_bytes=target,
        repetition=repetition,
        write_s=write_seconds,
        read_s=read_seconds,
        combined_s=write_seconds + read_seconds,
        write_peak_rss=write_peak,
        write_increment_rss=max(0, write_peak - baseline),
        read_peak_rss=read_peak,
        read_increment_rss=max(0, read_peak - read_baseline),
        bytes=sum(sizes),
        file_sizes=sizes,
        file_rows=counts,
        types=types,
        correct=True,
    )
    # Only this invocation's verified output directory can be removed.
    assert directory.resolve().parent == SCRATCH.resolve()
    for path in directory.iterdir():
        path.unlink()
    directory.rmdir()
    return result


def configurations(kind, small):
    broad = [
        (w, s, t)
        for w in ["json", "orjson", "duckdb"]
        for s in ["record", "batch", "rows"]
        for t in TARGETS
    ]
    broad.extend(("copy", "native", t) for t in TARGETS)
    finalists = [
        (w, s, t)
        for w, s in [("orjson", "batch"), ("duckdb", "batch"), ("copy", "native")]
        for t in TARGETS
    ]
    if small:
        return list(dict.fromkeys((w, s, 1_000_000) for w, s, _ in broad))
    if kind == "arrow":
        return list(dict.fromkeys(broad + finalists))
    return finalists


def run_case(kind, shape, small, repetitions):
    n = 1000 if small else (900_000 if shape == "flat" else 45_000)
    table = fixture(n, shape)
    # Calibrate row counts outside all timers, independently of the fixed batch size.
    average = statistics.mean(
        len(orjson.dumps(record(i, n, shape))) + 1 for i in range(1000)
    )
    row_limits = {target: max(1, round(target / average)) for target in TARGETS}
    con = duckdb.connect(config={"threads": 1})
    if kind == "dict":
        source = table.to_pylist()
        del table
    elif kind == "relation":
        con.from_arrow(table).create("source_data")
        source = con.table("source_data")
        del table
    else:
        source = table
    configs = configurations(kind, small)
    for repetition in range(repetitions + 1):
        # Rotation gives each candidate a changing position; zero is discarded warmup.
        offset = repetition % len(configs)
        for writer, sizing, target in configs[offset:] + configs[:offset]:
            result = trial(
                source,
                kind,
                n,
                shape,
                writer,
                sizing,
                target,
                row_limits[target],
                repetition,
                con,
            )
            if repetition:
                with RESULTS.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(result) + "\n")
            limit_label = (
                f"{row_limits[target]} rows"
                if sizing == "rows"
                else f"{target / 1e6:g}MB"
            )
            print(
                f"{kind} {shape} {n}: {writer}/{sizing} "
                f"{limit_label} trial {repetition}: "
                f"write {result['write_s']:.3f}s read {result['read_s']:.3f}s",
                flush=True,
            )
    con.close()


def main():
    global RESULTS
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--kind", choices=["dict", "arrow", "relation"], default="arrow"
    )
    parser.add_argument("--shape", choices=["flat", "nested"], default="flat")
    parser.add_argument("--small", action="store_true")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--compatibility", action="store_true")
    parser.add_argument("--output", type=Path, default=RESULTS)
    args = parser.parse_args()
    RESULTS = args.output.resolve()
    SCRATCH.mkdir(exist_ok=True)
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    if args.compatibility:
        compatibility_probes()
    elif args.all:
        assert not RESULTS.exists(), (
            "Preserve previous results before starting a new complete run"
        )
        for small in [True, False]:
            for kind in ["arrow", "dict", "relation"]:
                for shape in ["flat", "nested"]:
                    command = [
                        sys.executable,
                        __file__,
                        "--kind",
                        kind,
                        "--shape",
                        shape,
                        "--repetitions",
                        str(args.repetitions),
                        "--output",
                        str(RESULTS),
                    ]
                    if small:
                        command.append("--small")
                    subprocess.run(command, check=True)
    elif args.probe:
        metadata = dict(
            python=sys.version,
            platform=platform.platform(),
            duckdb=duckdb.__version__,
            pyarrow=pa.__version__,
            orjson=orjson.__version__,
            psutil=psutil.__version__,
            processor=platform.processor(),
            logical_cpus=psutil.cpu_count(),
            physical_memory_bytes=psutil.virtual_memory().total,
            threads=1,
            batch_rows=BATCH,
            reader_options=JSON_READER_OPTIONS,
            capabilities=probe(),
            source_sha256={
                name: hashlib.sha256(
                    (ROOT / "src/assquack/_materialization" / name).read_bytes()
                ).hexdigest()
                for name in [
                    "fragments.py",
                    "json_reader.py",
                    "schema.py",
                    "staging.py",
                ]
            },
        )
        (RESULTS.parent / "ndjson-2026-10-28-environment.json").write_text(
            json.dumps(metadata, indent=2)
        )
        print(json.dumps(metadata, indent=2))
    else:
        run_case(args.kind, args.shape, args.small, args.repetitions)


if __name__ == "__main__":
    main()
