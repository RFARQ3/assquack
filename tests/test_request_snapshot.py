"""Keep each request's values separate from later calls and configuration changes.

MAD.Prefect's attribute-formatting tests establish sibling option isolation and
reuse of original templates. The argument tests extend that protection to the
agreed Assquack call snapshot; MAD.Prefect does not deep-copy call arguments.
"""

import asyncio
from datetime import timedelta

import pytest

from assquack import AssquackConfig, asset
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
    "argument_source", ["positional", "keyword", "bound", "default"]
)
def test_request_keeps_nested_arguments_as_they_were_when_created(argument_source):
    filters = {"ids": [1, 2]}

    # A mutable default is intentional: it must not rewrite a previous request.
    @asset("bronze.orders")
    def source(filters=filters):
        raise AssertionError("Creating requests must not execute the producer")

    if argument_source == "positional":
        request = source._request((filters,), {})
    elif argument_source == "keyword":
        request = source._request((), {"filters": filters})
    elif argument_source == "bound":
        request = source.with_arguments(filters=filters)._request((), {})
    else:
        request = source._request((), {})

    filters["ids"].append(3)

    assert request.arguments == ({"ids": [1, 2]},)
    original = source._request((), {"filters": {"ids": [1, 2]}})
    assert resolve_request(request).asset_id == resolve_request(original).asset_id


def test_request_keeps_nested_keyword_only_arguments_as_they_were_when_created():
    @asset("bronze.orders")
    def source(*, filters: dict[str, list[int]]):
        raise AssertionError("Creating requests must not execute the producer")

    filters = {"ids": [1, 2]}
    request = source._request((), {"filters": filters})
    filters["ids"].append(3)

    assert request.keyword_arguments == {"filters": {"ids": [1, 2]}}


def test_reusing_arguments_for_another_call_does_not_rewrite_the_first_request():
    @asset(
        "bronze.{selection[endpoint]}",
        export="raw/{selection[endpoint]}.parquet",
    )
    def source(selection: dict[str, str]):
        raise AssertionError("Creating requests must not execute the producer")

    selection = {"endpoint": "orders"}
    first = source._request((selection,), {})
    selection["endpoint"] = "customers"
    second = source._request((selection,), {})

    assert resolve_request(first).table.table_name == "orders"
    assert resolve_request(first).export == "raw/orders.parquet"
    assert first.arguments == ({"endpoint": "orders"},)
    assert resolve_request(second).table.table_name == "customers"
    assert resolve_request(second).export == "raw/customers.parquet"
    assert second.arguments == ({"endpoint": "customers"},)
    assert resolve_request(first).asset_id != resolve_request(second).asset_id


@pytest.mark.parametrize("config_source", ["default", "asset", "call"])
def test_request_captures_effective_config(tmp_path, config_source):
    attached = AssquackConfig(home=tmp_path / "attached")
    override = AssquackConfig(home=tmp_path / "override")

    @asset("main.orders", config=attached if config_source != "default" else None)
    def source():
        raise AssertionError("Capturing configuration must not execute the producer")

    request = source._request(
        (), {}, config=override if config_source == "call" else None
    )
    expected = {
        "default": AssquackConfig().home,
        "asset": attached.home,
        "call": override.home,
    }
    assert request.config.home == expected[config_source]

    attached.home = tmp_path / "changed-attached"
    override.home = tmp_path / "changed-override"
    assert request.config.home == expected[config_source]


def test_call_captures_arguments_and_config_before_awaiting(tmp_path):
    @asset("main.{selection[endpoint]}")
    def source(selection):
        return [{"endpoint": selection["endpoint"]}]

    selection = {"endpoint": "orders"}
    config = AssquackConfig(home=tmp_path)
    pending = source(selection, assquack_config=config)
    selection["endpoint"] = "customers"
    config.attachment_name = "changed"
    result = asyncio.run(pending)

    assert result.table.table_name == "orders"
    assert result.config.attachment_name == "assquack"
    assert asyncio.run(result.query()).fetchall()[0][2] == "orders"
