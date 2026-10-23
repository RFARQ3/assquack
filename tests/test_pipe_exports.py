import asyncio
import csv
import json
from pathlib import Path
from unittest.mock import MagicMock

import duckdb
import pytest

from assquack import AssquackConfig, ExportsConfig, asset
from assquack._errors import ExportError
from assquack._exports.parquet import CsvExportWriter, JsonExportWriter
from assquack._exports.targets import parse_export_targets
from assquack._exports.writers import export_writer
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
    config = AssquackConfig(home=tmp_path)
    targets = parse_export_targets(alias, asset_name="customer_summary", config=config)
    assert [t.format for t in targets] == ["parquet", "csv", "json"]
    assert [t.role for t in targets] == ["default", "legacy", "legacy"]
    assert [t.logical_uri for t in targets] == [
        f"mad://{stem}.{fmt}" for fmt in ("parquet", "csv", "json")
    ]
    assert [t.resolved_uri for t in targets] == [
        str(Path(config.exports.base_uri or "") / f"{stem}.{fmt}")
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

    @asset(declaration, table="main.orders")
    def orders():
        nonlocal called
        called = True
        return [{"id": 1}]

    with pytest.raises(ExportError):
        asyncio.run(orders(assquack_config=AssquackConfig(home=tmp_path)))
    assert not called
    assert not (tmp_path / "dev" / "assquack.duckdb").exists()


@pytest.mark.parametrize("format_name", ["csv", "json", "parquet"])
def test_structured_extensionless_alias(tmp_path: Path, format_name: str) -> None:
    (target,) = parse_export_targets(
        {"uri": "bronze/orders", "format": format_name},
        asset_name="orders",
        config=AssquackConfig(home=tmp_path),
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

    @asset(alias, table="main.orders")
    def orders():
        nonlocal calls
        calls += 1
        yield rows[:1]
        yield rows[1:]

    config = AssquackConfig(home=tmp_path, exports=ExportsConfig(enabled=True))
    result = asyncio.run(orders.cache_first()(assquack_config=config))
    output = Path(config.exports.base_uri or "")
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

    # A missing secondary manifest must prevent a cache hit, even when the file remains.
    with duckdb.connect(str(config.resolved_database_path)) as db:
        db.execute("DELETE FROM assquack_meta.exports WHERE format = 'json'")
    refreshed = asyncio.run(orders.cache_first()(assquack_config=config))
    assert not refreshed.cached and calls == 2
    assert refreshed.run_id != result.run_id


@pytest.mark.parametrize("failure_format", ["csv", "json"])
def test_partial_failure_keeps_only_completed_manifests_and_retries(
    tmp_path: Path, monkeypatch, failure_format: str
) -> None:
    calls = 0

    @asset("orders.parquet|csv|json", table="main.orders")
    def orders():
        nonlocal calls
        calls += 1
        return [{"id": calls}]

    writer = CsvExportWriter if failure_format == "csv" else JsonExportWriter
    original = writer._copy

    def fail(*args):
        raise RuntimeError("synthetic output failure")

    monkeypatch.setattr(writer, "_copy", fail)
    config = AssquackConfig(home=tmp_path, exports=ExportsConfig(enabled=True))
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
    assert not (Path(config.exports.base_uri or "") / "orders.json").exists()
    monkeypatch.setattr(writer, "_copy", original)
    retry = asyncio.run(orders.cache_first()(assquack_config=config))
    assert not retry.cached and calls == 2
    assert asyncio.run(orders.cache_first()(assquack_config=config)).cached


@pytest.mark.parametrize("scheme", ["abfss", "az", "azure"])
def test_all_formats_use_azure_auth_and_native_copy(
    tmp_path: Path, monkeypatch, scheme: str
) -> None:
    monkeypatch.setattr("assquack._exports.azure_credentials.getenv", lambda _: None)
    service = "dfs" if scheme == "abfss" else "blob"
    config = AssquackConfig(
        home=tmp_path,
        exports=ExportsConfig(
            base_uri=f"{scheme}://exampleaccount.{service}.core.windows.net/lake",
            azure_auth="credential_chain",
        ),
    )
    targets = parse_export_targets(
        "orders.parquet|csv|json", asset_name="orders", config=config
    )
    for target in targets:
        connection = MagicMock(spec=duckdb.DuckDBPyConnection)
        export_writer(target.format, config.exports).write(
            connection, TableReference("main", "orders"), target
        )
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
    assert result.export_parquet(path.as_uri()) == path.as_uri()
    assert path.read_bytes().startswith(b"PAR1")
    for invalid in ("orders.csv", "parquet|csv"):
        with pytest.raises(ExportError):
            result.export_parquet(invalid)


def test_empty_extraction_writes_all_formats(tmp_path: Path) -> None:
    @asset("empty.parquet|csv|json", table="main.empty")
    def empty():
        return []

    config = AssquackConfig(home=tmp_path, exports=ExportsConfig(enabled=True))
    result = asyncio.run(empty(assquack_config=config))
    assert result.row_count == 0
    output = Path(config.exports.base_uri or "")
    assert (output / "empty.json").read_text() == ""
    with (output / "empty.csv").open(newline="") as file:
        assert len(list(csv.reader(file))) == 1
    with duckdb.connect(str(config.resolved_database_path)) as db:
        assert db.execute(
            "SELECT count(*) FROM assquack_meta.exports WHERE row_count = 0"
        ).fetchone() == (3,)


def test_nested_json_retains_objects_and_lists(tmp_path: Path) -> None:
    @asset("nested.json", table="main.nested")
    def nested():
        return [
            {
                "id": 1,
                "details": {"name": "café", "values": [1, 2]},
                "items": [{"code": "A"}],
            }
        ]

    config = AssquackConfig(home=tmp_path, exports=ExportsConfig(enabled=True))
    asyncio.run(nested(assquack_config=config))
    output = Path(config.exports.base_uri or "") / "nested.json"
    row = json.loads(output.read_text(encoding="utf-8"))
    assert row["details"] == {"name": "café", "values": [1, 2]}
    assert row["items"] == [{"code": "A"}]


def test_custom_options_rejected_instead_of_ignored(tmp_path: Path) -> None:
    config = AssquackConfig(
        home=tmp_path, exports=ExportsConfig(options={"header": False})
    )
    with pytest.raises(ExportError, match="Custom export options"):
        parse_export_targets("csv", asset_name="orders", config=config)
