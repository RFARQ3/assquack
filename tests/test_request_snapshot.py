"""Keep MAD.Prefect option isolation and ordinary Python argument references."""

import asyncio
from datetime import timedelta

import pytest

from assquack import AssquackConfig, FileSystemDestination, asset
from assquack._options import ExportSpec
from assquack._pipeline.resolve import resolve_request


def test_sibling_requests_keep_their_own_formatted_options():
    @asset(
        "bronze.{endpoint}",
        name="source_{endpoint}",
        export={"uri": "raw/{endpoint}.parquet", "format": "parquet"},
    )
    def source(endpoint: str):
        raise AssertionError("Creating requests must not execute the producer")

    orders = source.with_arguments(endpoint="orders")
    customers = source.with_arguments(endpoint="customers")
    first = orders._request((), {})
    second = customers._request((), {})
    overridden = orders._request((), {"endpoint": "returns"})

    assert resolve_request(first).table.table_name == "orders"
    assert resolve_request(first).asset_name == "source_orders"
    assert resolve_request(first).export == {
        "uri": "raw/orders.parquet",
        "format": "parquet",
    }
    assert resolve_request(second).export == {
        "uri": "raw/customers.parquet",
        "format": "parquet",
    }
    assert resolve_request(overridden).export == {
        "uri": "raw/returns.parquet",
        "format": "parquet",
    }
    assert orders.export == resolve_request(first).export
    assert customers.export == resolve_request(second).export
    assert source.table == "bronze.{endpoint}"
    assert source.export == {"uri": "raw/{endpoint}.parquet", "format": "parquet"}


def test_option_overrides_do_not_change_an_existing_request_or_base_templates():
    @asset(
        "bronze.{endpoint}",
        export="raw/{endpoint}.parquet",
        cache_expiration=timedelta(hours=1),
    )
    def source(endpoint: str):
        raise AssertionError("Creating requests must not execute the producer")

    first = source._request((), {"endpoint": "orders"})
    export: ExportSpec = {"uri": "processed/{endpoint}.csv", "format": "csv"}
    configured = source.with_options(
        table="silver.{endpoint}", export=export, cache_expiration=None
    )
    export["uri"] = "changed.csv"
    second = configured._request((), {"endpoint": "customers"})

    assert resolve_request(first).table.schema_name == "bronze"
    assert resolve_request(first).export == "raw/orders.parquet"
    assert resolve_request(first).use_cache
    assert resolve_request(first).cache_expiration == timedelta(hours=1)
    assert resolve_request(second).table.schema_name == "silver"
    assert resolve_request(second).export == {
        "uri": "processed/customers.csv",
        "format": "csv",
    }
    assert not resolve_request(second).use_cache
    assert source.export == "raw/{endpoint}.parquet"


def test_export_declaration_changes_do_not_rewrite_an_existing_request():
    export: ExportSpec = {"uri": "raw/{endpoint}.parquet", "format": "parquet"}

    @asset("bronze.{endpoint}", export=export)
    def source(endpoint: str):
        raise AssertionError("Creating requests must not execute the producer")

    request = source._request((), {"endpoint": "orders"})
    export["uri"] = "changed.csv"
    export["format"] = "csv"

    assert resolve_request(request).export == {
        "uri": "raw/orders.parquet",
        "format": "parquet",
    }
    assert resolve_request(source._request((), {"endpoint": "customers"})).export == {
        "uri": "raw/customers.parquet",
        "format": "parquet",
    }


@pytest.mark.parametrize(
    "argument_source", ["positional", "keyword", "keyword_only", "bound", "default"]
)
def test_pending_call_uses_current_nested_arguments(tmp_path, argument_source):
    filters = {"ids": [1, 2]}

    @asset("bronze.orders")
    def source(filters=filters):
        assert filters is original_filters
        return [{"id": number} for number in filters["ids"]]

    @asset("bronze.orders")
    def keyword_source(*, filters):
        assert filters is original_filters
        return [{"id": number} for number in filters["ids"]]

    original_filters = filters
    config = AssquackConfig(home=tmp_path)
    if argument_source == "positional":
        pending = source(filters, assquack_config=config)
    elif argument_source == "keyword":
        pending = source(filters=filters, assquack_config=config)
    elif argument_source == "keyword_only":
        pending = keyword_source(filters=filters, assquack_config=config)
    elif argument_source == "bound":
        pending = source.with_arguments(filters=filters)(assquack_config=config)
    else:
        pending = source(assquack_config=config)

    filters["ids"].append(3)
    result = asyncio.run(pending)
    assert result.row_count == 3
    assert asyncio.run(result.query("SELECT id ORDER BY id")).fetchall() == [
        (1,),
        (2,),
        (3,),
    ]


@pytest.mark.parametrize("config_source", ["default", "asset", "call"])
def test_request_uses_effective_config_reference(tmp_path, config_source):
    attached = AssquackConfig(home=tmp_path / "attached")
    override = AssquackConfig(home=tmp_path / "override")

    @asset("main.orders", config=attached if config_source != "default" else None)
    def source():
        raise AssertionError("Binding configuration must not execute the producer")

    request = source._request(
        (), {}, config=override if config_source == "call" else None
    )
    if config_source == "default":
        assert request.config.home == AssquackConfig().home
    else:
        assert request.config is (override if config_source == "call" else attached)


def test_call_resolves_arguments_and_config_when_awaited(tmp_path):
    @asset("main.{selection[endpoint]}", export="{selection[endpoint]}.parquet")
    def source(selection):
        return [{"endpoint": selection["endpoint"]}]

    selection = {"endpoint": "orders"}
    config = AssquackConfig(home=tmp_path, exports=str(tmp_path / "first"))
    pending = source(selection, assquack_config=config)
    selection["endpoint"] = "customers"
    config.exports = FileSystemDestination(base_uri=str(tmp_path / "second"))
    expected = resolve_request(source._request((selection,), {}, config=config))
    result = asyncio.run(pending)

    assert result.table.table_name == "customers"
    assert result.asset_id == expected.asset_id
    assert result.config is config
    assert asyncio.run(result.query()).fetchall()[0][2] == "customers"
    assert (tmp_path / "second/customers.parquet").is_file()
    assert not (tmp_path / "first").exists()
