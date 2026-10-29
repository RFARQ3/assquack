"""Self-contained, offline typed-input write benchmark; see adjacent methodology."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import msvcrt
import platform
import random
import shutil
import statistics
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pyarrow as pa

ROOT = Path(__file__).resolve().parents[1]
SIZES = {"smaller": [100000], "larger": [100000]}
TARGETS = [65536, 262144, 1048576]
BATCHES = [2048]


@contextmanager
def timing_lock():
    with (ROOT / ".tmp-fragment-benchmark-timing.lock").open("a+b") as handle:
        announced = False
        while True:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if not announced:
                    print("Waiting for shared timing lock", flush=True)
                    announced = True
                time.sleep(0.5)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def fields(shape):
    result = [
        ("id", pa.int64(), "int64", lambda i: i),
        ("text", pa.string(), "object", lambda i: 'café 東京 "\\\n' + "x" * 48),
        ("number", pa.float64(), "float64", lambda i: i / 4),
        ("optional", pa.int64(), "Int64", lambda i: None if i % 7 == 0 else i),
        ("active", pa.bool_(), "bool", lambda i: i % 2 == 0),
        (
            "nested",
            pa.struct([("first", pa.int64()), ("later", pa.int64())]),
            "object",
            lambda i: (
                None
                if i % 19 == 0
                else {"first": i, "later": None if i % 3 == 0 else i + 1}
            ),
        ),
        (
            "items",
            pa.list_(pa.int64()),
            "object",
            lambda i: (
                None if i % 23 == 0 else ([] if i % 17 == 0 else [i, None, i + 1])
            ),
        ),
    ]
    result.extend(
        [
            ("category", pa.string(), "object", lambda i: "synthetic café / 東京"),
            (
                "description",
                pa.string(),
                "object",
                lambda i: "Readable payload — naïve résumé " * 3,
            ),
            ("flags", pa.list_(pa.bool_()), "object", lambda i: [True, False, None]),
            ("region", pa.string(), "object", lambda i: "Sydney"),
        ]
    )
    if shape == "larger":
        result.append(
            (
                "details",
                pa.struct(
                    [
                        (
                            "owner",
                            pa.struct(
                                [
                                    ("name", pa.string()),
                                    (
                                        "address",
                                        pa.struct(
                                            [
                                                ("city", pa.string()),
                                                ("postcode", pa.int64()),
                                            ]
                                        ),
                                    ),
                                ]
                            ),
                        ),
                        (
                            "events",
                            pa.list_(
                                pa.struct([("code", pa.int64()), ("note", pa.string())])
                            ),
                        ),
                    ]
                ),
                "object",
                lambda i: {
                    "owner": {
                        "name": "Zoë 東京",
                        "address": {
                            "city": "Montréal",
                            "postcode": None if i % 7 == 0 else 2000,
                        },
                    },
                    "events": [
                        {"code": i, "note": "arrived ✓"},
                        {"code": None, "note": "café"},
                    ],
                },
            )
        )
        result.extend(
            (
                f"field_{j}",
                pa.string(),
                "object",
                lambda i, j=j: f"{i}:{j}:" + "é東京x" * 24,
            )
            for j in range(6)
        )
    return result


def fixture(source, shape, n):
    specs = fields(shape)
    if source == "pandas":
        return pd.DataFrame(
            {
                name: pd.Series([fn(i) for i in range(n)], dtype=dtype)
                for name, _, dtype, fn in specs
            }
        )
    schema = pa.schema([(name, dtype) for name, dtype, _, _ in specs])
    arrays = [
        pa.array([fn(i) for i in range(n)], type=dtype) for _, dtype, _, fn in specs
    ]
    if source == "table":
        return pa.Table.from_arrays(arrays, schema=schema)
    return pa.RecordBatch.from_arrays(arrays, schema=schema)


def fingerprint(row):
    # JSON canonicalization retains int/float distinctions and every null/key.
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False).encode(
            "utf-8"
        )
    ).digest()


def expected_hashes(shape, n):
    specs = fields(shape)
    return [fingerprint({name: fn(i) for name, _, _, fn in specs}) for i in range(n)]


def batches(source, obj, batch):
    for start in range(0, len(obj), batch):
        if source == "pandas":
            yield obj.iloc[start : start + batch].to_dict(orient="records")
        else:
            yield obj.slice(start, batch).to_pylist()


def lines(config, obj):
    for rows in batches(config["source"], obj, config["batch"]):
        for row in rows:
            yield (
                json.dumps(
                    row, ensure_ascii=False, separators=(",", ":"), allow_nan=False
                ).encode("utf-8")
                + b"\n"
            )


def write(config, obj, output):
    if config["method"] == "copy":
        with duckdb.connect(config={"threads": 1}) as con:
            relation = (
                con.from_df(obj)
                if config["source"] == "pandas"
                else con.from_arrow(obj)
            )
            relation.create_view("input_rows")
            destination = output.as_posix().replace("'", "''")
            con.execute(
                f"COPY input_rows TO '{destination}' "
                f"(FORMAT JSON, ARRAY false, FILE_SIZE_BYTES {config['target']})"
            )
        return
    output.mkdir()
    handle = None
    size = index = 0
    try:
        for line in lines(config, obj):
            if handle is None:
                handle = (output / f"part-{index}.json").open("xb", buffering=65536)
                size = 0
                index += 1
            handle.write(line)
            size += len(line)
            if size >= config["target"]:
                handle.close()
                handle = None
    finally:
        if handle is not None:
            handle.close()


def validate(files, expected):
    seen = bytearray(len(expected))
    for path in files:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                index = row["id"]
                assert type(index) is int and 0 <= index < len(expected)
                assert not seen[index] and fingerprint(row) == expected[index], row
                seen[index] = 1
    assert all(seen)


def trial(config, output):
    obj = fixture(config["source"], config["shape"], config["n"])
    expected = expected_hashes(config["shape"], config["n"])
    # Check actual typed input values before any writer can normalize them.
    for start, rows in enumerate(batches(config["source"], obj, 2048)):
        for offset, row in enumerate(rows):
            assert fingerprint(row) == expected[start * 2048 + offset]
    schema = str(obj.dtypes) if config["source"] == "pandas" else str(obj.schema)
    gc.collect()
    start = time.perf_counter()
    write(config, obj, output)
    files, sizes = [], []
    removed = 0
    for path in sorted(output.glob("*.json")):
        size = path.stat().st_size
        if size:
            files.append(path)
            sizes.append(size)
        else:
            path.unlink()
            removed += 1
    write_s = time.perf_counter() - start
    assert files and all(sizes)
    validate(files, expected)
    result = dict(
        config,
        write_s=write_s,
        bytes=sum(sizes),
        files=len(files),
        sizes=sizes,
        removed_empty_files=removed,
        retained_empty_files=0,
        correct=True,
        validated_records=len(expected),
        schema=schema,
    )
    # Only remove this invocation's own validated synthetic outputs.
    assert output.resolve().is_relative_to(
        (ROOT / ".tmp-typed-input-writes-20261029-152").resolve()
    )
    shutil.rmtree(output)
    return result


def configurations(smoke):
    for source in ["pandas", "table", "recordbatch"]:
        for shape, sizes in SIZES.items():
            for n in [1000] if smoke else sizes:
                for target in TARGETS[:1] if smoke else TARGETS:
                    methods = ["copy", "direct"]
                    for method in methods:
                        for batch in [None] if method == "copy" else BATCHES:
                            yield dict(
                                source=source,
                                shape=shape,
                                n=n,
                                target=target,
                                method=method,
                                batch=batch,
                            )


def summarize(results, output):
    groups = {}
    keys = ("source", "shape", "n", "target", "method", "batch")
    for row in results:
        groups.setdefault(tuple(row[key] for key in keys), []).append(row)
    summary = []
    for key, rows in groups.items():
        assert len(rows) == 3
        item: dict[str, Any] = dict(zip(keys, key, strict=True))
        for metric in ["write_s", "bytes", "files", "removed_empty_files"]:
            values = [row[metric] for row in rows]
            item[metric] = dict(
                median=statistics.median(values), min=min(values), max=max(values)
            )
        sizes = [size for row in rows for size in row["sizes"]]
        item["file_bytes"] = dict(
            min=min(sizes), median=statistics.median(sizes), max=max(sizes)
        )
        summary.append(item)
    output.with_suffix(".summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    assert args.run or args.smoke
    versions = dict(
        executable=sys.executable,
        python=sys.version,
        duckdb=duckdb.__version__,
        pandas=pd.__version__,
        pyarrow=pa.__version__,
    )
    print(json.dumps(versions, indent=2), flush=True)
    assert duckdb.__version__ == "1.5.2", versions
    run_id = uuid.uuid4().hex[:10]
    output = ROOT / "benchmarks/results" / f"typed-input-writes-20261029-152-{run_id}"
    scratch = ROOT / ".tmp-typed-input-writes-20261029-152" / run_id
    scratch.mkdir(parents=True)
    configs = list(configurations(args.smoke))
    metadata = dict(
        python=sys.version,
        executable=sys.executable,
        platform=platform.platform(),
        duckdb=duckdb.__version__,
        pandas=pd.__version__,
        pyarrow=pa.__version__,
        threads=1,
        fsync=False,
        trials=3,
        smoke=args.smoke,
        configurations=configs,
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        process_model="One process, new fixture and connection per trial",
        memory="Not sampled; psutil unavailable in this existing environment",
    )
    output.with_suffix(".metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    shapes = {}
    for shape in SIZES:
        specs = fields(shape)
        examples = [{name: fn(i) for name, _, _, fn in specs} for i in [0, 1]]
        total = sum(
            len(
                json.dumps(
                    {name: fn(i) for name, _, _, fn in specs},
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            )
            + 1
            for i in range(100000)
        )
        shapes[shape] = dict(
            rows=100000,
            root_fields=len(specs),
            maximum_container_depth_below_root=1 if shape == "smaller" else 3,
            average_serialized_bytes=total / 100000,
            total_bytes=total,
            examples=examples,
        )
    output.with_suffix(".fixtures.json").write_text(
        json.dumps(shapes, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(shapes, ensure_ascii=True, indent=2), flush=True)
    print(f"OUTPUT {output}", flush=True)
    warmups = [dict(c, repetition=-1) for c in configurations(True)]
    jobs: list[dict[str, Any]] = list(warmups)
    for repetition in range(3):
        ordered = [dict(c, repetition=repetition) for c in configs]
        random.Random(17300 + repetition).shuffle(ordered)
        jobs.extend(ordered)
    results = []
    for index, config in enumerate(jobs):
        with timing_lock():
            result = trial(config, scratch / f"trial-{index}")
        if config["repetition"] >= 0:
            results.append(result)
            with output.with_suffix(".jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(result) + "\n")
        if index % 10 == 0 or index == len(jobs) - 1:
            print(f"{index + 1}/{len(jobs)} completed", flush=True)
    summarize(results, output)
    scratch.rmdir()
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
