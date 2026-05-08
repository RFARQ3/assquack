import inspect

from assquack import AssquackAsset, asset


def test_asset_import_surface() -> None:
    assert callable(asset)


def test_asset_decorator_records_bootstrap_contract() -> None:
    @asset("bronze/orders.parquet", name="orders", table="bronze_orders")
    def orders() -> list[dict[str, int]]:
        return [{"id": 1}]

    assert isinstance(orders, AssquackAsset)
    assert orders.export == "bronze/orders.parquet"
    assert orders.name == "orders"
    assert orders.table == "bronze_orders"
    assert orders.mode == "replace"


def test_asset_decorator_accepts_async_functions_by_default() -> None:
    @asset("bronze/async-orders.parquet")
    async def orders() -> list[dict[str, int]]:
        return [{"id": 1}]

    assert isinstance(orders, AssquackAsset)
    assert inspect.iscoroutinefunction(orders.fn)
    assert orders.export == "bronze/async-orders.parquet"
