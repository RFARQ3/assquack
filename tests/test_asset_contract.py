import asyncio
from pathlib import Path
from typing import Any

import duckdb
import pytest

from assquack import AssquackAsset, AssquackConfig, MissingAssetSchemaWarning, asset


def source(region: str = "au") -> list[dict[str, str]]:
    return [{"region": region}]


@pytest.mark.parametrize(
    "export", [None, "orders.parquet", {"uri": "orders.csv", "format": "csv"}]
)
def test_positional_and_keyword_tables_are_equivalent(export: Any) -> None:
    positional = asset("sales.orders", mode="replace", name="orders", export=export)(
        source
    )
    keyword = asset(export=export, name="orders", table="sales.orders", mode="replace")(
        source
    )
    assert positional == keyword
    assert positional._request((), {}) == keyword._request((), {})


def test_missing_table_and_extra_positional_arguments_fail() -> None:
    with pytest.raises(TypeError, match="table"):
        asset()  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="table"):
        asset(export="orders.parquet")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        asset("sales.orders", "replace")  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="table"):
        AssquackAsset(fn=source)  # type: ignore[call-arg]


@pytest.mark.parametrize("table", [None, 1, {}, "", "  ", ".", "main.", ".orders"])
def test_invalid_tables_fail_at_definition(table: Any) -> None:
    with pytest.raises((TypeError, ValueError), match="table"):
        asset(table)
    with pytest.raises((TypeError, ValueError), match="table"):
        AssquackAsset(fn=source, table=table)


def test_table_templates_preserve_binding_and_identity() -> None:
    definition = asset(
        "sales.orders_{region}",
        name="orders_{region}",
        export="{region}/orders.parquet",
    )(source)
    au = definition.with_arguments(region="au")
    nz = au.with_arguments(region="nz")
    assert au.table == "sales.orders_au"
    assert nz.table == "sales.orders_nz"
    assert nz.name == "orders_nz"
    assert nz.export == "nz/orders.parquet"
    assert (
        nz._request((), {}).asset_id
        == definition._request((), {"region": "nz"}).asset_id
    )


def test_empty_rendered_table_fails_before_materialization(tmp_path: Path) -> None:
    called = False

    @asset("{target}")
    def rows(target: str) -> list[dict[str, int]]:
        nonlocal called
        called = True
        return [{"id": 1}]

    config = AssquackConfig(home=tmp_path)
    with pytest.raises(ValueError, match="table"):
        asyncio.run(rows("", assquack_config=config))
    assert not called
    assert config.database_path is not None
    assert not config.database_path.exists()


def test_positional_table_materializes_without_export(tmp_path: Path) -> None:
    orders = asset("sales.orders")(source)
    result = asyncio.run(orders(assquack_config=AssquackConfig(home=tmp_path)))
    assert orders.export is None
    assert asyncio.run(result.query("SELECT region FROM data")).fetchall() == [("au",)]


def test_default_name_tracks_table_templates_and_execution(tmp_path: Path) -> None:
    definition = asset("sales.orders_{region}")(source)
    assert definition.name == "sales.orders_au"
    bound = definition.with_arguments(region="nz")
    assert bound.name == "sales.orders_nz"
    assert bound._request((), {}).asset_id == definition._request(("nz",), {}).asset_id

    config = AssquackConfig(home=tmp_path)
    result = asyncio.run(bound(region="uk", assquack_config=config))
    assert result.asset_name == "sales.orders_uk"
    assert bound.name == "sales.orders_nz"
    cached = asyncio.run(definition.cache_first()("uk", assquack_config=config))
    assert cached.cached and cached.asset_name == result.asset_name
    assert cached.asset_id == result.asset_id
    with duckdb.connect(str(config.resolved_database_path)) as connection:
        assert connection.execute(
            "SELECT asset_name FROM assquack_meta.assets"
        ).fetchall() == [("sales.orders_uk",)]


def test_default_name_is_independent_of_export() -> None:
    definition = asset("sales.orders", export="elsewhere/custom.parquet")(source)
    assert definition.name == "sales.orders"
    assert (
        definition._request((), {}).asset_id
        == asset("sales.orders")(source)._request((), {}).asset_id
    )


@pytest.mark.parametrize("template", ["main.{target}", "{target}"])
def test_empty_default_table_can_be_rebound(template: str) -> None:
    @asset(template)
    def rows(target: str = "") -> list[dict[str, int]]:
        return [{"id": 1}]

    with pytest.raises(ValueError, match="table"):
        rows._request((), {})

    ready = rows.cache_first().with_arguments(target="orders")
    assert ready.table == template.format(target="orders")
    assert ready.with_arguments(target="").with_arguments(target="customers").table == (
        template.format(target="customers")
    )
    if template == "{target}":
        with pytest.warns(MissingAssetSchemaWarning):
            assert ready._request((), {}).table.table_name == "orders"
    else:
        assert ready._request((), {}).table.table_name == "orders"
