"""Offline relation-to-NDJSON diagnostic. Run with --run (or --smoke).

Reuses the preserved dictionary baseline's fixtures and Python writer algorithms.
Every worker holds the shared OS byte-range lock throughout its lifetime of work.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.metadata
import json
import msvcrt
import platform
import random
import statistics
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import dict_fragments_20261028 as baseline

ROOT = baseline.ROOT
duckdb = baseline.duckdb


@contextmanager
def timing_lock():
    # Lock the same byte as the other visible diagnostic chat. Closing the handle
    # releases the OS lock even if this process fails; never delete the lock file.
    with (ROOT / ".tmp-fragment-benchmark-timing.lock").open("a+b") as handle:
        waiting = False
        while True:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if not waiting:
                    print("Waiting for shared benchmark timing lock.", file=sys.stderr)
                    waiting = True
                time.sleep(0.5)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


class RelationSource:
    def __init__(self, relation, batch):
        self.relation = relation
        self.batch = batch

    def batches(self):
        columns = self.relation.columns
        cursor = self.relation.execute()
        while rows := cursor.fetchmany(self.batch):
            yield [dict(zip(columns, row, strict=True)) for row in rows]


def fingerprint(row):
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).digest()


def added_nulls(expected, actual):
    if isinstance(expected, dict):
        return sum(
            key not in expected and value is None for key, value in actual.items()
        ) + sum(added_nulls(value, actual[key]) for key, value in expected.items())
    if isinstance(expected, list):
        return sum(added_nulls(x, y) for x, y in zip(expected, actual, strict=True))
    return 0


def validate(rows, expected_hashes, n):
    seen = bytearray(n)
    for row in rows:
        index = row["id"]
        assert type(index) is int and 0 <= index < n and not seen[index]
        assert fingerprint(row) == expected_hashes[index * 32 : (index + 1) * 32], row
        seen[index] = 1
    assert all(seen), "Missing records"


def trial(config, directory):
    """Prepare a stable relation, time each phase separately, then validate it."""
    directory.mkdir(parents=True)
    fixture = directory / "source.ndjson"
    with fixture.open("x", encoding="utf-8", newline="\n") as handle:
        for i in range(config["n"]):
            handle.write(
                baseline.dumps(baseline.record(i, config["n"], config["shape"])) + "\n"
            )
    con = duckdb.connect(config={"threads": 1})
    con.read_json(str(fixture), format="newline_delimited", **baseline.OPTIONS).create(
        "prepared"
    )
    relation = con.table("prepared")
    schema = dict(zip(relation.columns, map(str, relation.types), strict=True))

    # Hash actual typed relation values in bounded batches. This avoids retaining
    # a whole-input list of Python records and checks scalar types as well as values.
    expected_hashes = bytearray(config["n"] * 32)
    padding = normalized = 0
    for batch in RelationSource(relation, 2048).batches():
        for row in batch:
            original = baseline.record(row["id"], config["n"], config["shape"])
            assert baseline.equivalent(original, row)
            padding += added_nulls(original, row)
            normalized += original != row
            expected_hashes[row["id"] * 32 : (row["id"] + 1) * 32] = fingerprint(row)
    fixture.unlink()
    gc.collect()

    output = directory / "fragments"
    start = time.perf_counter()
    if config["method"] == "copy":
        relation.create_view("input_rows", replace=True)
        path = output.as_posix().replace("'", "''")
        con.execute(
            f"COPY input_rows TO '{path}' "
            f"(FORMAT JSON, ARRAY false, FILE_SIZE_BYTES {config['target']})"
        )
        con.execute("DROP VIEW input_rows")
    else:
        baseline.write(RelationSource(relation, config["batch"]), config, output)
    # A completed fragment set must contain no empty inputs. Charge discovery and
    # removal to every route's write time, and retain removal counts in results.
    removed = 0
    files = []
    for path in sorted(output.rglob("*.json")):
        if path.stat().st_size == 0:
            path.unlink()
            removed += 1
        else:
            files.append(path)
    write_s = time.perf_counter() - start
    sizes = [path.stat().st_size for path in files]
    assert files and all(sizes)
    con.close()

    def fragment_rows():
        for path in files:
            with path.open(encoding="utf-8") as handle:
                yield from (json.loads(line) for line in handle)

    validate(fragment_rows(), expected_hashes, config["n"])
    gc.collect()
    start = time.perf_counter()
    reader = duckdb.connect(config={"threads": 1})
    imported = reader.read_json(
        [str(path) for path in files],  # pyright: ignore[reportArgumentType]
        format="newline_delimited",
        **baseline.OPTIONS,
    )
    read_schema = dict(zip(imported.columns, map(str, imported.types), strict=True))
    inference_s = time.perf_counter() - start
    imported.create("candidate")
    read_s = time.perf_counter() - start
    assert schema == read_schema, (schema, read_schema)
    validate(
        (
            row
            for batch in RelationSource(reader.table("candidate"), 2048).batches()
            for row in batch
        ),
        expected_hashes,
        config["n"],
    )
    reader.close()
    return dict(
        config,
        write_s=write_s,
        read_s=read_s,
        inference_s=inference_s,
        bytes=sum(sizes),
        files=len(files),
        sizes=sizes,
        removed_empty_files=removed,
        retained_empty_files=0,
        preparation_added_null_keys=padding,
        preparation_normalized_rows=normalized,
        writer_added_null_keys=0,
        schema=schema,
        read_schema=read_schema,
        correct=True,
        fragments=str(output),
    )


def configurations():
    for shape, sizes in baseline.SIZES.items():
        for n in sizes:
            for target in baseline.TARGETS:
                for method in ["copy", "jsonlines", "direct"]:
                    for batch in [None] if method == "copy" else baseline.BATCHES:
                        yield dict(
                            shape=shape, n=n, target=target, method=method, batch=batch
                        )


def summarize(results, output):
    groups = {}
    for result in results:
        key = tuple(result[k] for k in ("shape", "n", "target", "method", "batch"))
        groups.setdefault(key, []).append(result)
    summary = []
    for key, rows in groups.items():
        assert len(rows) == 3
        item: dict[str, Any] = dict(
            zip(("shape", "n", "target", "method", "batch"), key, strict=True)
        )
        for metric in (
            "write_s",
            "read_s",
            "inference_s",
            "bytes",
            "files",
            "removed_empty_files",
        ):
            values = [row[metric] for row in rows]
            item[metric] = dict(
                median=statistics.median(values), min=min(values), max=max(values)
            )
        sizes = [size for row in rows for size in row["sizes"]]
        item["file_bytes"] = dict(
            min=min(sizes), median=statistics.median(sizes), max=max(sizes)
        )
        item["preparation_added_null_keys"] = rows[0]["preparation_added_null_keys"]
        summary.append(item)
    output.with_suffix(".summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--worker")
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    if args.worker:
        with timing_lock():
            print(json.dumps(trial(json.loads(args.worker), args.directory)))
        return
    assert args.run or args.smoke
    run_id = uuid.uuid4().hex[:10]
    scratch = ROOT / ".tmp-relation-fragments-20261028" / run_id
    scratch.mkdir(parents=True)
    output = ROOT / "benchmarks/results" / f"relation-fragments-20261028-{run_id}"
    configs = list(configurations())
    if args.smoke:
        configs = [c for c in configs if c["n"] in (1000, 100) and c["target"] == 65536]
    metadata = dict(
        python=sys.version,
        executable=sys.executable,
        platform=platform.platform(),
        cpu=platform.processor(),
        ram=baseline.psutil.virtual_memory().total,
        duckdb=duckdb.__version__,
        jsonlines=importlib.metadata.version("jsonlines"),
        threads=1,
        fsync=False,
        trials=3,
        configurations=configs,
        smoke=args.smoke,
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        baseline_sha256=hashlib.sha256(
            Path(baseline.__file__).read_bytes()
        ).hexdigest(),
        read_options=baseline.OPTIONS,
        scratch=str(scratch),
    )
    output.with_suffix(".metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    print(f"OUTPUT {output}", flush=True)
    warmups = [
        dict(
            shape="narrow",
            n=1000,
            target=65536,
            method=m,
            batch=128 if m != "copy" else None,
            repetition=-1,
        )
        for m in ("copy", "jsonlines", "direct")
    ]
    jobs: list[dict[str, Any]] = list(warmups)
    for repetition in range(3):
        ordered = [dict(c, repetition=repetition) for c in configs]
        random.Random(9300 + repetition).shuffle(ordered)
        jobs.extend(ordered)
    results = []
    for index, config in enumerate(jobs):
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--worker",
                json.dumps(config),
                "--directory",
                str(scratch / f"trial-{index}"),
            ],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        result = json.loads(completed.stdout)
        if config["repetition"] >= 0:
            results.append(result)
            with output.with_suffix(".jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(result) + "\n")
        if index % 10 == 0 or index == len(jobs) - 1:
            print(f"{index + 1}/{len(jobs)} completed", flush=True)
    summarize(results, output)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
