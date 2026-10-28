import warnings

import pytest

from assquack import MissingAssetSchemaWarning
from assquack._api import _resolve_table
from assquack._storage.tables import TableReference


def test_missing_schema_warns_and_uses_main() -> None:
    with pytest.warns(
        MissingAssetSchemaWarning,
        match="does not define a DuckDB schema; using 'main'",
    ):
        table = _resolve_table(
            "orders",
            asset_name="orders",
        )

    assert table == TableReference("main", "orders")


def test_qualified_table_uses_its_declared_schema_without_warning() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", MissingAssetSchemaWarning)
        table = _resolve_table(
            "bronze.orders",
            asset_name="orders",
        )

    assert table == TableReference("bronze", "orders")
