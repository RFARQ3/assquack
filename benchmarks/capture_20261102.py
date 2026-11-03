"""Offline fragment capture comparison. No production implementation is modified."""

from __future__ import annotations

import argparse
import ast
import ctypes
import hashlib
import itertools
import json
import msvcrt
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import traceback
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import duckdb  # noqa: E402

from assquack._materialization import fragments as implementation  # noqa: E402

REVISION = "5ab0e2192422b3bce3df58fd860d5c6dd4ba5007"
OUTPUT = ROOT / "benchmarks" / "capture-results-20261102"
PATHS = ("current", "historical", "direct")
SIZES = {
    "flat": (100_000, 1_000_000, 10_000_000),
    "nested": (100_000, 1_000_000),
    "wide": (10_000, 100_000, 1_000_000),
}


class ProcessMemory(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        *[
            (name, ctypes.c_size_t)
            for name in (
                "PeakWorkingSetSize",
                "WorkingSetSize",
                "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage",
                "PagefileUsage",
                "PeakPagefileUsage",
                "PrivateUsage",
            )
        ],
    ]


def memory():
    # The pseudo-handle belongs to this executing worker, never its venv launcher.
    counters = ProcessMemory()
    counters.cb = ctypes.sizeof(counters)
    function = ctypes.windll.psapi.GetProcessMemoryInfo
    function.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    function.restype = ctypes.c_int
    if not function(ctypes.c_void_p(-1), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    return [counters.WorkingSetSize, counters.PrivateUsage]


def historical_classes():
    source = subprocess.check_output(
        ["git", "show", f"{REVISION}:src/assquack/_materialization/fragments.py"],
        cwd=ROOT,
        text=True,
    )
    tree = ast.parse(source)
    writer = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    namespace = vars(implementation).copy()
    exec(
        compile(ast.Module(body=[writer], type_ignores=[]), REVISION, "exec"), namespace
    )
    historical = namespace["NDJSONFragmentWriter"]

    # Change only the COPY invocation; retain historical counting and finalization.
    # `relation` is local to the executing method, so replacement scans can find it.
    direct_tree = ast.parse(source)
    direct_class = next(n for n in direct_tree.body if isinstance(n, ast.ClassDef))
    method = next(
        n
        for n in direct_class.body
        if isinstance(n, ast.FunctionDef) and n.name == "write_relation"
    )
    call = next(
        n
        for n in ast.walk(method)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "query"
    )
    call.func = ast.Attribute(
        value=ast.Name(id="duckdb", ctx=ast.Load()), attr="execute", ctx=ast.Load()
    )
    call.args = call.args[1:]
    for node in ast.walk(call):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            node.value = node.value.replace("COPY fragment_source", "COPY relation")
    ast.fix_missing_locations(direct_class)
    direct_namespace = vars(implementation).copy()
    exec(
        compile(ast.Module(body=[direct_class], type_ignores=[]), "direct", "exec"),
        direct_namespace,
    )
    return historical, direct_namespace["NDJSONFragmentWriter"], source


def projection(shape: str, plan: str) -> list[str]:
    value = "s.id" if plan == "scan" else "s.id + d.offset_value"
    fields = [f"{value} AS id"]
    if shape == "flat":
        fields += [
            "id % 2 = 0 AS active",
            "id::DOUBLE / 4 AS amount",
            "(id / 100)::DECIMAL(18,2) AS price",
            "DATE '2020-01-01' + (id % 1000)::INTEGER AS day",
            "CASE WHEN id % 7 = 0 THEN NULL ELSE id END AS optional",
            "repeat(md5(id::VARCHAR), 1) AS short_text",
            "repeat(md5(id::VARCHAR), 4) AS long_text",
        ]
    elif shape == "nested":
        fields += [
            "id % 2 = 0 AS active",
            "struct_pack(name := md5(id::VARCHAR), value := id / 4) AS detail",
            "CASE WHEN id % 7 = 0 THEN NULL WHEN id % 11 = 0 THEN [] ELSE "
            "list_transform(range(4), x -> struct_pack(key := x, "
            "label := md5((id+x)::VARCHAR))) END AS items",
        ]
    elif shape == "wide":
        fields += [f"id + {i} AS number_{i}" for i in range(49)]
        fields += [f"repeat(md5((id+{i})::VARCHAR), 2) AS text_{i}" for i in range(50)]
    elif shape == "edge":
        fields += [
            "CASE WHEN id % 2 = 0 THEN NULL ELSE 'café 東京' || chr(10) || "
            "chr(34) || chr(92) END AS escaped",
            "170141183460469231731687303715884105727::HUGEINT AS huge",
            "12345678901234567890.123456789::DECIMAL(38,9) AS decimal_value",
            "TIMESTAMPTZ '2020-01-01 12:00:00+10' AS zoned",
            "INTERVAL '1 month 2 days 3 seconds' AS duration",
            "struct_pack(a := id, b := [NULL, id]) AS nested",
        ]
    return fields


def query(shape: str, plan: str) -> str:
    source = (
        "source s"
        if plan == "scan"
        else "source s JOIN dimension d ON s.id % 1000 = d.key"
    )
    return "SELECT " + ", ".join(projection(shape, plan)) + " FROM " + source


def configure(connection, spill: Path):
    connection.execute("SET threads = 4")
    connection.execute("SET memory_limit = '8GB'")
    connection.execute("SET TimeZone = 'UTC'")
    connection.execute("SET temp_directory = ?", [str(spill)])


def settings(connection):
    return dict(
        connection.execute(
            "SELECT name, value FROM duckdb_settings() WHERE name IN "
            "('threads','memory_limit','temp_directory','TimeZone',"
            "'preserve_insertion_order','storage_compatibility_version')"
        ).fetchall()
    )


def validate_records(connection, shape, rows):
    # Every fixture row is determined by its unique integer ID. Both source plans
    # return the same values because dimension offsets are zero. Compare all fields
    # while streaming; exact ID coverage also detects missing or duplicate records.
    packed_fields = []
    for field in projection(shape, "scan"):
        expression, name = field.rsplit(" AS ", 1)
        packed_fields.append(f"{name} := {expression}")
    expected = "struct_pack(" + ", ".join(packed_fields) + ")"
    observed = connection.execute(
        "SELECT count(*), count(DISTINCT id), min(id), max(id), "
        f"count(*) FILTER (WHERE s IS DISTINCT FROM {expected}) "
        "FROM observed s"
    ).fetchone()
    assert observed == (rows, rows, 0, rows - 1, 0), observed


def validate(connection, sql, directory, fragments, rows, shape):
    files = sorted(directory.glob("*.ndjson"))
    assert {str(p) for p in files} == {f.path for f in fragments}
    assert (directory / "_COMPLETE").is_file()
    assert set(directory.iterdir()) == {*files, directory / "_COMPLETE"}
    assert all(f.row_count > 0 and Path(f.path).stat().st_size > 0 for f in fragments)
    assert sum(f.row_count for f in fragments) == rows
    if not rows:
        assert not files and not fragments
        return
    if rows <= 1000:
        expected_rows = {
            record["id"]: record
            for (encoded,) in connection.execute(
                f"SELECT to_json(t) FROM ({sql}) t"
            ).fetchall()
            for record in [json.loads(encoded, parse_float=Decimal)]
        }
        observed_rows = 0
        for path in files:
            file_rows = 0
            with path.open(encoding="utf-8") as source:
                for line in source:
                    record = json.loads(line, parse_float=Decimal)
                    assert record == expected_rows.pop(record["id"]), record
                    file_rows += 1
            assert file_rows > 0
            observed_rows += file_rows
        assert not expected_rows and observed_rows == rows
        return
    paths_sql = ", ".join(implementation.quote_literal(str(p)) for p in files)
    expected = connection.sql(sql)
    columns_sql = ", ".join(
        implementation.quote_literal(name)
        + ":"
        + implementation.quote_literal(str(kind))
        for name, kind in zip(expected.columns, expected.types, strict=True)
    )
    connection.execute(
        f"CREATE OR REPLACE TEMP VIEW observed AS SELECT * "
        f"FROM read_json([{paths_sql}], columns={{{columns_sql}}}, "
        "format='newline_delimited')"
    )
    validate_records(connection, shape, rows)
    connection.execute("DROP VIEW observed")


def worker(case):
    historical, direct, historical_source = historical_classes()
    work = OUTPUT / "scratch" / case["name"]
    work.mkdir(parents=True, exist_ok=False)
    result: dict[str, Any] = dict(case, pid=os.getpid(), version=duckdb.__version__)
    assert duckdb.__version__ == "1.5.2", duckdb.__version__
    result["historical_sha256"] = hashlib.sha256(historical_source.encode()).hexdigest()
    connection = duckdb.connect(str(work / "assquack.duckdb"))
    configure(connection, work / "source-spill")
    duckdb.set_default_connection(connection)
    connection.execute(
        "CREATE TABLE source AS SELECT range AS id FROM range(?)", [case["rows"]]
    )
    connection.execute(
        "CREATE TABLE dimension AS SELECT range AS key, "
        "0::BIGINT AS offset_value FROM range(1000)"
    )
    sql = query(case["shape"], case["plan"])
    result["sql"] = sql
    result["source_settings"] = settings(connection)
    result["plan_text"] = connection.sql(sql).explain()
    writer_class = {
        "current": implementation.NDJSONFragmentWriter,
        "historical": historical,
        "direct": direct,
    }[case["path"]]
    writer = writer_class(work / "fragments", target_bytes=case["target"])
    original_connect = duckdb.connect

    # Only the controlled profile adapts the extra bridge connection's settings.
    # Record bridge defaults outside capture using a disposable connection.
    with original_connect() as probe:
        if case["profile"] == "controlled":
            configure(probe, work / "bridge-spill")
        result["bridge_settings"] = (
            settings(probe) if case["path"] == "current" else None
        )

    def bridge_connect(*args, **kwargs):
        opened = original_connect(*args, **kwargs)
        configure(opened, work / "bridge-spill")
        return opened

    if case["profile"] == "controlled":
        duckdb.connect = bridge_connect
    if case.get("read_only"):
        connection.execute("BEGIN TRANSACTION READ ONLY")
    relation = connection.sql(sql)
    result["types"] = list(zip(relation.columns, map(str, relation.types), strict=True))
    samples = []
    stop = threading.Event()
    active = threading.Event()

    def sample():
        while not stop.is_set():
            if active.is_set():
                samples.append((time.perf_counter(), *memory()))
            stop.wait(0.005)

    sampler = None
    baseline = [0, 0]
    if case["lens"] == "memory":
        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        baseline = memory()
        result["baseline_bytes"] = baseline
        active.set()
    started = time.perf_counter()
    try:
        result["written_rows"] = writer.write_relation(relation)
        completed = writer.finish()
        ended = time.perf_counter()
        if sampler:
            samples.append((ended, *memory()))
        result["elapsed_seconds"] = ended - started
    finally:
        active.clear()
        stop.set()
        if sampler:
            sampler.join()
        duckdb.connect = original_connect
    if sampler:
        peaks = [max([baseline[i], *(s[i + 1] for s in samples)]) for i in range(2)]
        result["peak_bytes"] = peaks
        result["increment_bytes"] = [
            p - b for p, b in zip(peaks, baseline, strict=True)
        ]
        result["samples"] = len(samples)
        times = sorted([started, *(s[0] for s in samples), ended])
        result["max_sample_gap_seconds"] = max(
            b - a for a, b in itertools.pairwise(times)
        )
    if case.get("read_only"):
        connection.execute("ROLLBACK")
    result["fragment_sizes"] = [Path(f.path).stat().st_size for f in completed]
    result["fragment_rows"] = [f.row_count for f in completed]
    result["validation_method"] = (
        "python_decimal" if case["rows"] <= 1000 else "streaming_fields_and_ids"
    )
    validation_started = time.perf_counter()
    try:
        validate(
            connection, sql, work / "fragments", completed, case["rows"], case["shape"]
        )
        result["validated"] = True
    except Exception:
        result["validated"] = False
        result["error"] = traceback.format_exc()
    result["validation_seconds"] = time.perf_counter() - validation_started
    connection.close()
    # Retain measurements, not gigabytes of regenerable synthetic output.
    assert work.resolve().is_relative_to((OUTPUT / "scratch").resolve())
    if result["validated"]:
        shutil.rmtree(work)
    return result


def run_case(case, destination):
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        json.dumps(case),
    ]
    process = subprocess.Popen(
        command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    cleanup_failed = False
    try:
        stdout, stderr = process.communicate(timeout=300)
        try:
            result = json.loads(stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            result = dict(case, error=stderr or stdout, exit_code=process.returncode)
    except subprocess.TimeoutExpired:
        # Kill the whole owned tree while its launcher still exists. Killing just
        # the Windows venv redirector can otherwise leave a native worker behind.
        termination = None
        if process.poll() is None:
            termination = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            cleanup_failed = termination.returncode != 0
        stdout = ""
        if not cleanup_failed:
            try:
                stdout, stderr = process.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                cleanup_failed = True
        result = dict(
            case,
            validated=False,
            timeout_seconds=300,
            error="Worker exceeded 300 seconds; capture/validation phase unknown.",
            launcher_pid=process.pid,
            cleanup_failed=cleanup_failed,
            termination_output=termination.stdout if termination else None,
        )
        # An environment pause can let the deadline expire after the child has
        # already finished. Preserve a complete returned result when available.
        if not cleanup_failed and stdout:
            try:
                recovered = json.loads(stdout.strip().splitlines()[-1])
                if recovered.get("name") == case["name"]:
                    result = recovered | {"controller_timeout_seconds": 300}
            except (ValueError, IndexError):
                pass
    with destination.open("a", encoding="utf-8") as output:
        output.write(json.dumps(result) + "\n")
    print(
        json.dumps(
            {
                k: result[k]
                for k in ("name", "elapsed_seconds", "peak_bytes", "validated", "error")
                if k in result
            }
        ),
        flush=True,
    )
    if cleanup_failed:
        raise RuntimeError("Verify the timed-out worker tree stopped before resuming.")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker")
    parser.add_argument("--gates", action="store_true")
    parser.add_argument("--compatibility", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--tier", type=int, choices=(0, 1, 2))
    parser.add_argument("--repetitions", type=int, default=6)
    args = parser.parse_args()
    if args.worker:
        case = json.loads(args.worker)
        try:
            result = worker(case)
        except Exception:
            result = dict(case, error=traceback.format_exc())
        print(json.dumps(result), flush=True)
        return
    OUTPUT.mkdir(exist_ok=True)
    (OUTPUT / "environment.json").write_text(
        json.dumps(
            {
                "platform": platform.platform(),
                "python": sys.version,
                "cpu": platform.processor(),
                "logical_cpus": os.cpu_count(),
                "duckdb": duckdb.__version__,
                "historical": REVISION,
                "idle_sleep_prevention": True,
            },
            indent=2,
        )
    )
    # Share the repository's existing timing lock with older benchmark runners.
    with (ROOT / ".tmp-fragment-benchmark-timing.lock").open("a+b") as lock:
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        filename = "gates.jsonl" if args.gates else f"tier-{args.tier}.jsonl"
        if args.compatibility:
            filename = "compatibility.jsonl"
        destination = OUTPUT / filename
        if destination.exists() and not args.resume:
            raise RuntimeError(f"Refusing to overwrite {destination}")
        completed_names = (
            {json.loads(line)["name"] for line in destination.read_text().splitlines()}
            if destination.exists()
            else set()
        )
        if args.compatibility:
            for path in PATHS:
                case = dict(
                    shape="flat",
                    rows=1000,
                    path=path,
                    lens="speed",
                    plan="scan",
                    profile="controlled",
                    target=1000,
                    read_only=True,
                    name=f"read-only-{path}",
                )
                run_case(case, destination)
            return
        if args.gates:
            for shape, rows, path, lens, plan in itertools.product(
                ("edge", "flat", "nested", "wide"),
                (0, 1, 1000),
                PATHS,
                ("speed", "memory"),
                ("scan", "join"),
            ):
                case = dict(
                    shape=shape,
                    rows=rows,
                    path=path,
                    lens=lens,
                    plan=plan,
                    profile="controlled",
                    target=1000,
                )
                case["name"] = f"gate-v4-{shape}-{rows}-{path}-{lens}-{plan}"
                result = run_case(case, destination)
            return
        gates = [
            json.loads(line)
            for line in (OUTPUT / "gates.jsonl").read_text().splitlines()
        ]
        assert len(gates) == 144
        assert all(g.get("validated") for g in gates if g["shape"] != "edge")
        permutations = list(itertools.permutations(PATHS))
        if args.repetitions == 3:
            permutations = [PATHS[index:] + PATHS[:index] for index in range(3)]
        shapes = list(SIZES.items())
        if args.tier == 2:
            # Exercise the largest row width first so resource limits surface early.
            shapes.reverse()
        for shape, sizes in shapes:
            if args.tier >= len(sizes):
                continue
            for plan, profile, lens in itertools.product(
                ("scan", "join"), ("faithful", "controlled"), ("speed", "memory")
            ):
                for trial in range(-1, args.repetitions):
                    for path in permutations[trial % len(permutations)]:
                        case = dict(
                            shape=shape,
                            rows=sizes[args.tier],
                            path=path,
                            lens=lens,
                            plan=plan,
                            profile=profile,
                            target=64_000_000,
                            trial=trial,
                        )
                        case["name"] = "-".join(map(str, case.values()))
                        if case["name"] in completed_names:
                            continue
                        run_case(case, destination)


if __name__ == "__main__":
    keep_awake = "--worker" not in sys.argv
    if keep_awake and not ctypes.windll.kernel32.SetThreadExecutionState(0x80000001):
        raise ctypes.WinError()
    try:
        main()
    finally:
        if keep_awake:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
