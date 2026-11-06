import asyncio
import csv
import json

import duckdb
import pytest

from assquack import AssquackConfig, FileSystemDestination, asset
from assquack._errors import ExportError
from assquack._exports.resolution import resolve_exports
from assquack._exports.writer import DuckDBFileWriter
from database_helpers import inspect_database


def materialized_result(tmp_path):
    @asset("main.orders")
    def orders():
        return [{"id": 1, "text": "quoted, café"}]

    return asyncio.run(
        orders(assquack_config=AssquackConfig(home=tmp_path, exports=[]))
    )


@pytest.mark.parametrize("format_name", ["parquet", "csv", "json"])
@pytest.mark.parametrize("as_object", [False, True])
def test_manual_formats_write_data_and_manifest(tmp_path, format_name, as_object):
    result = materialized_result(tmp_path)
    destination = (
        FileSystemDestination(
            base_uri=str(tmp_path / "reports"), path="orders", format=format_name
        )
        if as_object
        else f"reports/orders.{format_name}"
    )
    uri = result.export(destination)
    output = (
        tmp_path
        / ("reports" if as_object else "exports/reports")
        / f"orders.{format_name}"
    )
    assert uri == str(output)
    if format_name == "csv":
        with output.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert rows[0]["id"] == "1"
        assert rows[0]["text"] == "quoted, café"
    elif format_name == "json":
        assert json.loads(output.read_text(encoding="utf-8"))["id"] == 1
    else:
        with duckdb.connect() as db:
            assert db.execute("SELECT id FROM read_parquet(?)", [uri]).fetchall() == [
                (1,)
            ]
    with inspect_database(result.config) as db:
        assert db.execute(
            "SELECT run_id, row_count, format, resolved_uri FROM assquack_meta.exports"
        ).fetchall() == [(result.run_id, 1, format_name, str(output))]


@pytest.mark.parametrize(
    "path,format_name,filename",
    [
        (None, None, "main.orders.parquet"),
        (None, "csv", "main.orders.csv"),
        ("orders.json", None, "orders.json"),
        ("orders", None, "orders.parquet"),
        ("mad://orders", "json", "orders.json"),
    ],
)
def test_destination_owned_defaults(tmp_path, path, format_name, filename):
    result = materialized_result(tmp_path)
    destination = FileSystemDestination(
        base_uri=str(tmp_path), path=path, format=format_name
    )
    assert result.export(destination) == str(tmp_path / filename)
    assert destination._resolved is None


@pytest.mark.parametrize(
    "path,format_name", [("orders.csv", "json"), ("csv|json", "csv"), ("", None)]
)
def test_conflicting_manual_targets_fail_without_manifest(tmp_path, path, format_name):
    result = materialized_result(tmp_path)
    with pytest.raises(ExportError):
        result.export(
            FileSystemDestination(base_uri=str(tmp_path), path=path, format=format_name)
        )
    with inspect_database(result.config) as db:
        assert db.execute("SELECT count(*) FROM assquack_meta.exports").fetchone() == (
            0,
        )


def test_manual_defaults_do_not_change_automatic_targets_or_fingerprints(tmp_path):
    plain = FileSystemDestination(base_uri=str(tmp_path))
    manual = FileSystemDestination(
        base_uri=str(tmp_path), path="elsewhere.json", format="csv"
    )
    with (
        resolve_exports("orders.parquet|csv", "orders", [plain]) as first,
        resolve_exports("orders.parquet|csv", "orders", [manual]) as second,
    ):
        assert [target for _, target in first] == [target for _, target in second]


def test_manual_partial_failure_preserves_success_and_releases_state(
    tmp_path, monkeypatch
):
    result = materialized_result(tmp_path)
    destination = FileSystemDestination(
        base_uri=str(tmp_path), path="orders.parquet|csv|json"
    )
    original = DuckDBFileWriter.write
    attempts = []
    states = []

    def fail_csv(self, connection, source, target, uri):
        attempts.append(target.format)
        if target.format == "csv":
            raise ValueError("synthetic CSV failure")
        original(self, connection, source, target, uri)

    original_export = FileSystemDestination.export

    def capture(self, connection, source, target):
        states.append(self)
        original_export(self, connection, source, target)

    monkeypatch.setattr(FileSystemDestination, "export", capture)
    monkeypatch.setattr(DuckDBFileWriter, "write", fail_csv)
    with pytest.raises(ValueError, match="synthetic CSV"):
        result.export(destination)
    assert attempts == ["parquet", "csv"]
    assert all(state._resolved is None for state in states)
    with inspect_database(result.config) as db:
        assert db.execute("SELECT format FROM assquack_meta.exports").fetchall() == [
            ("parquet",)
        ]
        assert db.execute("SELECT id FROM main.orders").fetchall() == [(1,)]
    monkeypatch.setattr(DuckDBFileWriter, "write", original)
    assert result.export(destination) == [
        str(tmp_path / f"orders.{fmt}") for fmt in ("parquet", "csv", "json")
    ]


def test_disabled_automatic_export_does_not_resolve_manual_defaults(tmp_path):
    @asset("main.orders")
    def orders():
        return [{"id": 1}]

    destination = FileSystemDestination(base_uri=str(tmp_path), path="invalid.txt")
    result = asyncio.run(
        orders(assquack_config=AssquackConfig(home=tmp_path, exports=destination))
    )
    assert result.row_count == 1
    with pytest.raises(ExportError):
        result.export(destination)
