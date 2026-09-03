import warnings

import pytest

from assquack import MissingAssetSchemaWarning
from assquack._api import _resolve_table
from assquack._storage.tables import TableReference


@pytest.mark.parametrize("override", [None, "orders"])
def test_missing_schema_warns_and_uses_main(override: str | None) -> None:
    with pytest.warns(
        MissingAssetSchemaWarning,
        match="does not define a DuckDB schema; using 'main'",
    ):
        table = _resolve_table(
            override,
            asset_name="orders",
            asset_id="1234567890abcdef",
            has_arguments=False,
        )

    assert table == TableReference("main", "orders")


def test_qualified_table_uses_its_declared_schema_without_warning() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", MissingAssetSchemaWarning)
        table = _resolve_table(
            "bronze.orders",
            asset_name="orders",
            asset_id="1234567890abcdef",
            has_arguments=False,
        )

    assert table == TableReference("bronze", "orders")
