import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from assquack import AssquackConfig, FileSystemDestination, asset
from database_helpers import inspect_database


def test_disabled_exports_allow_cache_and_manual_export(tmp_path: Path) -> None:
    @asset(table="main.groups", export="bronze/groups.parquet")
    def groups():
        return [{"id": 1}]

    config = AssquackConfig(home=tmp_path, exports=None)
    result = asyncio.run(groups(assquack_config=config))
    assert asyncio.run(groups.cache_first()(assquack_config=config)).cached
    with inspect_database(config) as db:
        assert db.execute("SELECT count(*) FROM assquack_meta.exports").fetchone() == (
            0,
        )
    assert result.export("parquet") == str(tmp_path / "exports/main.groups.parquet")


def test_changed_destination_exports_cached_table_in_all_formats(
    tmp_path: Path,
) -> None:
    calls = 0

    @asset(table="main.orders", export="orders.parquet|csv|json")
    def orders():
        nonlocal calls
        calls += 1
        return [{"id": calls}]

    config = AssquackConfig(
        home=tmp_path,
        exports=FileSystemDestination(base_uri=str(tmp_path / "a")),
    )
    result = asyncio.run(orders.cache_first()(assquack_config=config))
    assert calls == 1
    original_run_id = result.run_id
    original_asset_id = result.asset_id
    config.exports = FileSystemDestination(base_uri=str(tmp_path / "b"))
    result = asyncio.run(orders.cache_first()(assquack_config=config))
    assert result.cached and calls == 1
    assert result.run_id == original_run_id
    assert result.asset_id == original_asset_id
    for folder in ("a", "b"):
        for suffix in ("parquet", "csv", "json"):
            assert (tmp_path / folder / f"orders.{suffix}").exists()
    assert asyncio.run(orders.cache_first()(assquack_config=config)).cached
    with inspect_database(config) as db:
        assert db.execute("SELECT count(*) FROM assquack_meta.exports").fetchone() == (
            6,
        )
        db.execute(
            "DELETE FROM assquack_meta.exports WHERE resolved_uri = ?",
            [str(tmp_path / "b/orders.csv")],
        )
    result = asyncio.run(orders.cache_first()(assquack_config=config))
    assert result.cached and calls == 1
    with inspect_database(config) as db:
        assert db.execute("SELECT count(*) FROM assquack_meta.exports").fetchone() == (
            6,
        )
    assert result.export("manual.parquet") == str(tmp_path / "exports/manual.parquet")
    assert not (tmp_path / "a/manual.parquet").exists()
    assert not (tmp_path / "b/manual.parquet").exists()


def test_config_accepts_destination_instance_and_rejects_old_fields() -> None:
    destination = FileSystemDestination(base_uri="./out")
    assert AssquackConfig(exports=destination).exports is destination
    for field in (
        "enabled",
        "options",
        "snapshot_runs",
        "writer",
        "credential_provider",
    ):
        with pytest.raises(ValidationError):
            FileSystemDestination.model_validate({"base_uri": "./out", field: True})
