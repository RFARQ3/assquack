import asyncio
import csv
import json
from pathlib import Path
from unittest.mock import MagicMock

import duckdb
import pytest

from assquack import AssquackConfig, FileSystemDestination, asset
from assquack._errors import ExportError
from assquack._exports.targets import parse_export_targets
from assquack._exports.writer.file import DuckDBFileWriter
from assquack._storage.tables import TableReference


@pytest.mark.parametrize(
    "alias,stem",
    [
        ("orders.parquet|csv|json", "orders"),
        ("mad://orders.parquet|csv|json", "orders"),
        ("mad://bronze/orders.parquet|csv|json", "bronze/orders"),
        ("parquet|csv|json", "customer_summary"),
        ("orders|csv|json", "orders"),
    ],
)
def test_pipe_aliases_expand_in_order(tmp_path: Path, alias: str, stem: str) -> None:
    targets = parse_export_targets(
        alias, asset_name="customer_summary", base_uri=str(tmp_path / "exports")
    )
    assert [t.format for t in targets] == ["parquet", "csv", "json"]
    assert [t.role for t in targets] == ["default", "legacy", "legacy"]
    assert [t.logical_uri for t in targets] == [
        f"mad://{stem}.{fmt}" for fmt in ("parquet", "csv", "json")
    ]
    assert [t.resolved_uri for t in targets] == [
        str(Path(str(tmp_path / "exports")) / f"{stem}.{fmt}")
        for fmt in ("parquet", "csv", "json")
    ]


@pytest.mark.parametrize(
    "declaration",
    [
        "",
        "orders.parquet|",
        "|json",
        "parquet||csv",
        "parquet|parquet",
        "orders.parquet|other.csv",
        "orders.xml",
        "orders.parquet|xml",
        {"uri": "orders.parquet", "format": "csv"},
        {"uri": "orders", "format": "xml"},
        {"uri": "orders.parquet|csv", "format": "parquet"},
    ],
)
def test_invalid_declarations_fail_before_extraction(
    tmp_path: Path, declaration
) -> None:
    called = False

    @asset(table="main.orders", export=declaration)
    def orders():
        nonlocal called
        called = True
        return [{"id": 1}]

    config = AssquackConfig(
        home=tmp_path, exports=FileSystemDestination(base_uri=str(tmp_path / "exports"))
    )
    with pytest.raises(ExportError):
        asyncio.run(orders(assquack_config=config))
    assert not called
    assert not (tmp_path / "dev" / "assquack.duckdb").exists()


@pytest.mark.parametrize("format_name", ["csv", "json", "parquet"])
def test_structured_extensionless_alias(tmp_path: Path, format_name: str) -> None:
    (target,) = parse_export_targets(
        {"uri": "bronze/orders", "format": format_name},
        asset_name="orders",
        base_uri=str(tmp_path / "exports"),
    )
    assert target.logical_uri == f"mad://bronze/orders.{format_name}"
    assert target.format == format_name


@pytest.mark.parametrize("alias", ["orders.parquet|csv|json", "parquet|csv|json"])
def test_actual_formats_contents_manifests_and_cache(
    tmp_path: Path, alias: str
) -> None:
    calls = 0
    rows = [
        {"id": 1, "text": 'comma, quote " and\nnewline', "optional": None},
        {"id": 2, "text": "café", "optional": "present"},
    ]

    @asset(table="main.orders", export=alias)
    def orders():
        nonlocal calls
        calls += 1
        yield rows[:1]
        yield rows[1:]

    config = AssquackConfig(
        home=tmp_path, exports=FileSystemDestination(base_uri=str(tmp_path / "exports"))
    )
    result = asyncio.run(orders.cache_first()(assquack_config=config))
    output = Path(str(tmp_path / "exports"))
    with duckdb.connect(str(config.resolved_database_path)) as db:
        parquet_rows = db.execute(
            "SELECT id, text, optional FROM read_parquet(?) ORDER BY id",
            [str(output / "orders.parquet")],
        ).fetchall()
        assert parquet_rows == [(r["id"], r["text"], r["optional"]) for r in rows]
        manifests = db.execute(
            "SELECT format, role, row_count, options_json, run_id "
            "FROM assquack_meta.exports ORDER BY created_at"
        ).fetchall()
    assert [(r[0], r[1], r[2]) for r in manifests] == [
        ("parquet", "default", 2),
        ("csv", "legacy", 2),
        ("json", "legacy", 2),
    ]
    assert [json.loads(r[3]) for r in manifests] == [
        {},
        {"header": True},
        {"array": False},
    ]
    assert {r[4] for r in manifests} == {result.run_id}

    json_rows = [
        json.loads(line)
        for line in (output / "orders.json").read_text(encoding="utf-8").splitlines()
    ]
    assert [{k: row[k] for k in rows[0]} for row in json_rows] == rows
    with (output / "orders.csv").open(encoding="utf-8", newline="") as file:
        csv_rows = list(csv.DictReader(file))
    assert [r["text"] for r in csv_rows] == [r["text"] for r in rows]
    assert csv_rows[0]["optional"] == ""
    cached = asyncio.run(orders.cache_first()(assquack_config=config))
    assert cached.cached and cached.run_id == result.run_id and calls == 1

    # A missing secondary manifest requires only that output to be retried.
    with duckdb.connect(str(config.resolved_database_path)) as db:
        db.execute("DELETE FROM assquack_meta.exports WHERE format = 'json'")
    refreshed = asyncio.run(orders.cache_first()(assquack_config=config))
    assert refreshed.cached and calls == 1
    assert refreshed.run_id == result.run_id


@pytest.mark.parametrize("failure_format", ["csv", "json"])
def test_partial_failure_keeps_only_completed_manifests_and_retries(
    tmp_path: Path, monkeypatch, failure_format: str
) -> None:
    calls = 0

    @asset(table="main.orders", export="orders.parquet|csv|json")
    def orders():
        nonlocal calls
        calls += 1
        return [{"id": calls}]

    original = DuckDBFileWriter.write

    def fail(self, connection, source, target, uri):
        if target.format == failure_format:
            raise RuntimeError("synthetic output failure")
        return original(self, connection, source, target, uri)

    monkeypatch.setattr(DuckDBFileWriter, "write", fail)
    config = AssquackConfig(
        home=tmp_path, exports=FileSystemDestination(base_uri=str(tmp_path / "exports"))
    )
    with pytest.raises(RuntimeError, match="synthetic output failure"):
        asyncio.run(orders.cache_first()(assquack_config=config))
    with duckdb.connect(str(config.resolved_database_path)) as db:
        assert db.execute("SELECT id FROM main.orders").fetchall() == [(1,)]
        assert db.execute("SELECT status FROM assquack_meta.runs").fetchall() == [
            ("success",)
        ]
        expected = (
            [("parquet",)] if failure_format == "csv" else [("parquet",), ("csv",)]
        )
        assert (
            db.execute(
                "SELECT format FROM assquack_meta.exports ORDER BY created_at"
            ).fetchall()
            == expected
        )
    assert not (Path(str(tmp_path / "exports")) / "orders.json").exists()
    monkeypatch.setattr(DuckDBFileWriter, "write", original)
    retry = asyncio.run(orders.cache_first()(assquack_config=config))
    assert retry.cached and calls == 1
    assert asyncio.run(orders.cache_first()(assquack_config=config)).cached


@pytest.mark.parametrize("scheme", ["abfss", "az", "azure"])
def test_all_formats_use_azure_auth_and_native_copy(
    tmp_path: Path, monkeypatch, scheme: str
) -> None:
    monkeypatch.setattr(
        "assquack._exports.authentication.credentials.getenv",
        {
            "AZURE_TENANT_ID": "tenant",
            "AZURE_CLIENT_ID": "client",
            "AZURE_CLIENT_SECRET": "synthetic-secret",
        }.get,
    )
    service = "dfs" if scheme == "abfss" else "blob"
    config = AssquackConfig(
        home=tmp_path,
        exports=FileSystemDestination(
            base_uri=f"{scheme}://exampleaccount.{service}.core.windows.net/lake",
            auth="credential_chain",
        ),
    )
    destination = config.export_destinations[0]
    assert isinstance(destination, FileSystemDestination)
    targets = destination.resolve("orders.parquet|csv|json", asset_name="orders")
    for target in targets:
        connection = MagicMock(spec=duckdb.DuckDBPyConnection)
        destination.export(connection, TableReference("main", "orders"), target)
        calls = connection.execute.call_args_list
        assert calls[0].args == ("LOAD azure",)
        assert calls[1].args[0].startswith("CREATE TEMPORARY SECRET")
        assert f"FORMAT {target.format}" in calls[2].args[0]
        assert calls[2].args[1] == {"uri": target.resolved_uri}
        assert calls[-1].args[0].startswith("DROP TEMPORARY SECRET")


def test_file_uri_and_explicit_parquet_validation(tmp_path: Path) -> None:
    @asset(table="main.orders")
    def orders():
        return [{"id": 1}]

    result = asyncio.run(orders(assquack_config=AssquackConfig(home=tmp_path)))
    path = tmp_path / "space and quote's.parquet"
    assert result.export_parquet(path.as_uri()) == str(path)
    assert path.read_bytes().startswith(b"PAR1")
    for invalid in ("orders.csv", "parquet|csv"):
        with pytest.raises(ExportError):
            result.export_parquet(invalid)


def test_empty_extraction_writes_all_formats(tmp_path: Path) -> None:
    @asset(table="main.empty", export="empty.parquet|csv|json")
    def empty():
        return []

    config = AssquackConfig(
        home=tmp_path, exports=FileSystemDestination(base_uri=str(tmp_path / "exports"))
    )
    result = asyncio.run(empty(assquack_config=config))
    assert result.row_count == 0
    output = Path(str(tmp_path / "exports"))
    assert (output / "empty.json").read_text() == ""
    with (output / "empty.csv").open(newline="") as file:
        assert len(list(csv.reader(file))) == 1
    with duckdb.connect(str(config.resolved_database_path)) as db:
        assert db.execute(
            "SELECT count(*) FROM assquack_meta.exports WHERE row_count = 0"
        ).fetchone() == (3,)


def test_nested_json_retains_objects_and_lists(tmp_path: Path) -> None:
    @asset(table="main.nested", export="nested.json")
    def nested():
        return [
            {
                "id": 1,
                "details": {"name": "café", "values": [1, 2]},
                "items": [{"code": "A"}],
            }
        ]

    config = AssquackConfig(
        home=tmp_path, exports=FileSystemDestination(base_uri=str(tmp_path / "exports"))
    )
    asyncio.run(nested(assquack_config=config))
    output = Path(str(tmp_path / "exports")) / "nested.json"
    row = json.loads(output.read_text(encoding="utf-8"))
    assert row["details"] == {"name": "café", "values": [1, 2]}
    assert row["items"] == [{"code": "A"}]


def test_custom_options_rejected_instead_of_ignored(tmp_path: Path) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FileSystemDestination.model_validate(
            {"base_uri": str(tmp_path), "options": {"header": False}}
        )
