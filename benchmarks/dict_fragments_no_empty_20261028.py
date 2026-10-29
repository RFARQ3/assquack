"""Offline dictionary-to-fragment benchmark; no production package imports.

Run: poetry run python benchmarks/dict_fragments_no_empty_20261028.py --run
Every invocation owns a UUID-named scratch tree and result files. See its report.
"""

from __future__ import annotations

import argparse
import contextlib
import gc
import hashlib
import io
import itertools
import json
import msvcrt
import platform
import random
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
# Reuse installed diagnostic dependencies, without modifying either environment.
sys.path.extend(
    [
        str(ROOT / ".tmp-normalization-benchmark/deps"),
        str(ROOT / ".tmp-mad-parquet-repro/venv/Lib/site-packages"),
    ]
)
import duckdb  # noqa: E402
import jsonlines  # noqa: E402
import psutil  # noqa: E402

TARGETS = [65536, 262144, 1048576]
BATCHES = [128, 2048]
SIZES = {"narrow": [1000, 10000, 100000], "wide": [100, 1000, 10000]}
OPTIONS: dict[str, Any] = dict(
    sample_size=-1,
    field_appearance_threshold=0.0,
    map_inference_threshold=-1,
    union_by_name=True,
    hive_partitioning=False,
)


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def record(i, n, shape):
    value: dict[str, Any] = dict(
        id=i,
        text='café 東京 "\\\n' + "x" * 48,
        number=i / 4,
        optional=None if i % 7 == 0 else i,
        active=i % 2 == 0,
        nested={"first": i},
        items=[i, None, i + 1],
    )
    # Late fields occur inside batches, and first appear after early batches.
    if i % 128 == 127:
        value["inside_late"] = f"late-{i}"
        value["nested"]["later"] = i
    if i >= n * 9 // 10:
        value["across_late"] = i
    if shape == "wide":
        value.update({f"field_{j}": f"{i}:{j}:" + "é東京x" * 40 for j in range(12)})
    return value


class Source:
    def __init__(self, config):
        self.config = config
        self.generation_s = 0.0
        self.rows = None
        if config["source"] == "list":
            self.rows = [
                record(i, config["n"], config["shape"]) for i in range(config["n"])
            ]

    def batches(self):
        c = self.config
        for start in range(0, c["n"], c["batch"] or 2048):
            end = min(start + (c["batch"] or 2048), c["n"])
            if self.rows is not None:
                yield self.rows[start:end]
            else:
                before = time.perf_counter()
                rows = [record(i, c["n"], c["shape"]) for i in range(start, end)]
                self.generation_s += time.perf_counter() - before
                yield rows


class Memory:
    def __enter__(self):
        self.process = psutil.Process()
        self.baseline = self.process.memory_info().rss
        self.peak = self.baseline
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.monitor)
        self.thread.start()
        return self

    def monitor(self):
        while not self.stop.wait(0.001):
            self.peak = max(self.peak, self.process.memory_info().rss)

    def __exit__(self, *_):
        self.peak = max(self.peak, self.process.memory_info().rss)
        self.stop.set()
        self.thread.join()


def copy_batch(con, data, directory, target, array):
    relation = con.read_json(
        io.StringIO(data), format="array" if array else "newline_delimited", **OPTIONS
    )
    relation.create_view("input_rows", replace=True)
    path = directory.as_posix().replace("'", "''")
    con.execute(
        f"COPY input_rows TO '{path}' "
        f"(FORMAT JSON, ARRAY false, FILE_SIZE_BYTES {target})"
    )
    con.execute("DROP VIEW input_rows")


def write_original(source, config, directory):
    directory.mkdir()
    method, target = config["method"], config["target"]
    if method.startswith("duck"):
        con = duckdb.connect(config={"threads": 1})
        try:
            if method == "duck_all":
                rows = source.rows
                if rows is None:
                    rows = list(itertools.chain.from_iterable(source.batches()))
                copy_batch(con, dumps(rows), directory / "all", target, True)
            else:
                for index, rows in enumerate(source.batches()):
                    data = "".join(dumps(row) + "\n" for row in rows)
                    copy_batch(con, data, directory / f"batch-{index}", target, False)
        finally:
            con.close()
        return

    handle = writer = None
    size = index = 0
    buffer = bytearray()
    try:
        for rows in source.batches():
            for row in rows:
                if handle is None:
                    handle = (directory / f"part-{index}.json").open(
                        "xb", buffering=65536
                    )
                    if method == "jsonlines":
                        writer = jsonlines.Writer(
                            # jsonlines' attrs-generated constructor supports dumps.
                            handle,
                            dumps=lambda obj: dumps(obj).encode("utf-8"),  # pyright: ignore[reportCallIssue]
                        )
                if method == "jsonlines":
                    # The matching method branch initializes writer above.
                    size += writer.write(row)  # pyright: ignore[reportOptionalMemberAccess]
                else:
                    data = (dumps(row) + "\n").encode("utf-8")
                    buffer.extend(data)
                    size += len(data)
                    if len(buffer) >= 65536:
                        handle.write(buffer)
                        buffer.clear()
                # Record-boundary rotation can exceed the target by one record.
                if size >= target:
                    if buffer:
                        handle.write(buffer)
                        buffer.clear()
                    handle.close()
                    handle = None
                    size = 0
                    index += 1
        if handle is not None:
            if buffer:
                handle.write(buffer)
            handle.close()
    finally:
        if handle is not None and not handle.closed:
            handle.close()


@contextlib.contextmanager
def timing_lock():
    """Serialize benchmark workers across the two visible diagnostic chats."""
    with (ROOT / ".tmp-fragment-benchmark-timing.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        waiting = False
        while True:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if not waiting:
                    print(
                        "Waiting for shared benchmark timing lock.",
                        file=sys.stderr,
                        flush=True,
                    )
                    waiting = True
                time.sleep(0.25)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def write(source, config, directory):
    """Finish flush/close and remove native COPY empties before registration."""
    write_original(source, config, directory)
    removed = 0
    for path in directory.rglob("*.json"):
        if path.stat().st_size == 0:
            path.unlink()
            removed += 1
    return removed


def null_profile(files, config):
    """Count explicit nulls separately from absent keys padded by COPY."""
    counts = dict(explicit_null_values=0, added_null_keys=0, added_null_bytes=0)

    def compare(expected, actual):
        if expected is None:
            counts["explicit_null_values"] += 1
        elif isinstance(expected, dict):
            for key, value in actual.items():
                if key not in expected:
                    assert value is None
                    counts["added_null_keys"] += 1
                    counts["added_null_bytes"] += len(
                        (dumps(key) + ":null,").encode("utf-8")
                    )
                else:
                    compare(expected[key], value)
        elif isinstance(expected, list):
            for left, right in zip(expected, actual, strict=True):
                compare(left, right)

    rows_per_file = []
    for path in files:
        rows = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            actual = json.loads(line)
            compare(record(actual["id"], config["n"], config["shape"]), actual)
            rows += 1
        rows_per_file.append(rows)
    return dict(counts, rows_per_file=rows_per_file)


def equivalent(expected, actual):
    """Permit only added null keys and exact numeric widening (int -> float)."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or not expected.keys() <= actual.keys():
            return False
        return all(equivalent(v, actual[k]) for k, v in expected.items()) and all(
            actual[k] is None for k in actual.keys() - expected.keys()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(equivalent(x, y) for x, y in zip(expected, actual, strict=True))
        )
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(expected) is type(actual) and expected == actual
    return expected == actual and (
        type(expected) is type(actual)
        or (isinstance(expected, (int, float)) and isinstance(actual, (int, float)))
    )


def validate(rows, config):
    seen = bytearray(config["n"])
    normalized = 0
    for actual in rows:
        i = actual["id"]
        assert 0 <= i < config["n"] and not seen[i], ("duplicate/id", i)
        expected = record(i, config["n"], config["shape"])
        assert equivalent(expected, actual), ("value loss", expected, actual)
        normalized += expected != actual
        seen[i] = 1
    assert all(seen), "missing records"
    return normalized


def trial(config, directory):
    process = psutil.Process()
    imported_rss = process.memory_info().rss
    source = Source(config)
    gc.collect()
    with Memory() as write_memory:
        start = time.perf_counter()
        removed_empty_files = write(source, config, directory)
        wall = time.perf_counter() - start
    files = sorted(directory.rglob("*.json"))
    sizes = [path.stat().st_size for path in files]
    assert sizes and min(sizes) > 0
    profile = null_profile(files, config)
    normalized = validate(
        (
            json.loads(line)
            for path in files
            for line in path.read_text(encoding="utf-8").splitlines()
        ),
        config,
    )
    del source.rows
    gc.collect()
    with Memory() as read_memory:
        start = time.perf_counter()
        con = duckdb.connect(config={"threads": 1})
        relation = con.read_json(
            # DuckDB accepts file lists at runtime; its stub omits that overload.
            [str(path) for path in files],  # pyright: ignore[reportArgumentType]
            format="newline_delimited",
            **OPTIONS,
        )
        schema = dict(zip(relation.columns, map(str, relation.types), strict=True))
        inference_s = time.perf_counter() - start
        relation.create("candidate")
        read_s = time.perf_counter() - start
    # Fetching JSON after materialization validates native JSON/struct values alike.
    cursor = con.execute("SELECT to_json(t) FROM candidate t")

    def decoded():
        while batch := cursor.fetchmany(2048):
            yield from (json.loads(row[0]) for row in batch)

    read_normalized = validate(decoded(), config)
    con.close()
    result = dict(
        config,
        write_s=wall - source.generation_s,
        write_wall_s=wall,
        generation_excluded_s=source.generation_s,
        inference_s=inference_s,
        read_s=read_s,
        imported_rss=imported_rss,
        write_baseline_rss=write_memory.baseline,
        write_peak_rss=write_memory.peak,
        read_baseline_rss=read_memory.baseline,
        read_peak_rss=read_memory.peak,
        sizes=sizes,
        bytes=sum(sizes),
        files=len(files),
        max_overshoot=max(0, max(sizes) - config["target"]),
        under_half_target=sum(s < config["target"] / 2 for s in sizes),
        normalized_records=normalized,
        read_normalized_records=read_normalized,
        schema=schema,
        correct=True,
        removed_empty_files=removed_empty_files,
        retained_empty_files=0,
        null_profile=profile,
    )
    # Only remove the directory created by this worker, beneath its UUID scratch root.
    assert (
        directory.resolve().parent.parent
        == (ROOT / ".tmp-dict-fragments-no-empty-20261028").resolve()
    )
    shutil.rmtree(directory)
    return result


def configurations():
    for shape, sizes in SIZES.items():
        for n in sizes:
            for target in TARGETS:
                for method in ["jsonlines", "direct", "duck_batch", "duck_all"]:
                    for batch in [None] if method == "duck_all" else BATCHES:
                        yield dict(
                            shape=shape,
                            n=n,
                            target=target,
                            method=method,
                            batch=batch,
                            source="list",
                        )
        for method in ["jsonlines", "direct", "duck_batch", "duck_all"]:
            for batch in [None] if method == "duck_all" else BATCHES:
                yield dict(
                    shape=shape,
                    n=sizes[1],
                    target=TARGETS[1],
                    method=method,
                    batch=batch,
                    source="stream",
                )


def semantic_probe(scratch):
    rows: list[dict[str, Any]] = [
        dict(
            id=i,
            varying=[1, "text", {"k": 2}, None][i % 4],
            nested={"a": i},
            number=i if i % 2 else i / 2,
        )
        for i in range(260)
    ]
    rows[127]["within"] = "present"
    rows[259]["late"] = {"nested": [None, True, "東京"]}
    results = []
    for method in ["jsonlines", "direct", "duck_batch", "duck_all"]:
        config = dict(
            method=method,
            target=65536,
            batch=128,
            n=len(rows),
            source="list",
            shape="narrow",
        )
        source = Source(config)
        source.rows = rows
        directory = scratch / ("probe-" + method)
        write(source, config, directory)
        paths = sorted(directory.rglob("*.json"))
        actual = [
            json.loads(line)
            for path in paths
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
        actual.sort(key=lambda row: row["id"])
        assert len(actual) == len(rows)
        assert all(equivalent(x, y) for x, y in zip(rows, actual, strict=True))
        con = duckdb.connect(config={"threads": 1})
        rel = con.read_json(
            [str(p) for p in paths],  # pyright: ignore[reportArgumentType]
            format="newline_delimited",
            **OPTIONS,
        )
        rel.create("candidate")
        readback = [
            json.loads(r[0])
            for r in con.execute(
                "SELECT to_json(t) FROM candidate t ORDER BY id"
            ).fetchall()
        ]
        assert all(equivalent(x, y) for x, y in zip(rows, readback, strict=True))
        results.append(
            dict(
                method=method,
                correct=True,
                normalized_records=sum(
                    x != y for x, y in zip(rows, actual, strict=True)
                ),
                schema=dict(zip(rel.columns, map(str, rel.types), strict=True)),
            )
        )
        con.close()
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--worker")
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    if args.worker:
        with timing_lock():
            result = trial(json.loads(args.worker), args.directory)
        print(json.dumps(result))
        return
    assert args.run
    assert duckdb.__version__ == "1.5.2", duckdb.__version__
    run_id = uuid.uuid4().hex[:10]
    scratch = ROOT / ".tmp-dict-fragments-no-empty-20261028" / run_id
    scratch.mkdir(parents=True)
    output = ROOT / "benchmarks/results" / f"dict-fragments-no-empty-20261028-{run_id}"
    configs = list(configurations())
    with timing_lock():
        probes = semantic_probe(scratch)
    metadata = dict(
        python=sys.version,
        platform=platform.platform(),
        duckdb=duckdb.__version__,
        psutil=psutil.__version__,
        threads=1,
        configurations=configs,
        trials=3,
        scratch=str(scratch),
        executable=sys.executable,
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        cpu=platform.processor(),
        ram=psutil.virtual_memory().total,
        semantic_probes=probes,
        fsync=False,
        lock_path=str(ROOT / ".tmp-fragment-benchmark-timing.lock"),
        baseline_sha256=hashlib.sha256(
            (ROOT / "benchmarks/dict_fragments_20261028.py").read_bytes()
        ).hexdigest(),
    )
    output.with_suffix(".metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    print(f"OUTPUT {output}", flush=True)
    # One discarded small warmup for each architecture; each measured worker is fresh.
    warmups: list[dict[str, Any]] = [
        dict(
            shape="narrow",
            n=1000,
            target=65536,
            method=m,
            batch=128 if m != "duck_all" else None,
            source="list",
            repetition=-1,
        )
        for m in ["jsonlines", "direct", "duck_batch", "duck_all"]
    ]
    jobs = list(warmups)
    for repetition in range(3):
        ordered = [dict(c, repetition=repetition) for c in configs]
        random.Random(9100 + repetition).shuffle(ordered)
        jobs.extend(ordered)
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
            capture_output=True,
            text=True,
        )
        if completed.stderr:
            print(completed.stderr.strip(), flush=True)
        result = json.loads(completed.stdout)
        if config["repetition"] >= 0:
            with output.with_suffix(".jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(result) + "\n")
        if index % 10 == 0 or index == len(jobs) - 1:
            print(
                f"{index + 1}/{len(jobs)} {config['method']} "
                f"{config['shape']}/{config['n']}: {result['write_s']:.3f}s",
                flush=True,
            )
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
