import asyncio
from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError

from assquack import AssquackConfig, FileSystemDestination, asset
from assquack._errors import ExportError


def test_empty_destinations_skip_exports_and_allow_cache(tmp_path: Path) -> None:
    @asset("bronze/groups.parquet", table="main.groups")
    def groups():
        return [{"id": 1}]

    config = AssquackConfig(home=tmp_path, exports=[])
    result = asyncio.run(groups(assquack_config=config))
    assert asyncio.run(groups.cache_first()(assquack_config=config)).cached
    with duckdb.connect(str(config.resolved_database_path)) as db:
        assert db.execute("SELECT count(*) FROM assquack_meta.exports").fetchone() == (
            0,
        )
    assert result.export_parquet() == str(tmp_path / "dev/exports/groups.parquet")


def test_multi_destination_formats_manifests_cache_and_explicit(tmp_path: Path) -> None:
    calls = 0

    @asset("orders.parquet|csv|json", table="main.orders")
    def orders():
        nonlocal calls
        calls += 1
        return [{"id": calls}]

    config = AssquackConfig(
        home=tmp_path,
        exports=[
            FileSystemDestination(base_uri=str(tmp_path / name)) for name in ("a", "b")
        ],
    )
    result = asyncio.run(orders.cache_first()(assquack_config=config))
    assert calls == 1
    for folder in ("a", "b"):
        for suffix in ("parquet", "csv", "json"):
            assert (tmp_path / folder / f"orders.{suffix}").exists()
    assert asyncio.run(orders.cache_first()(assquack_config=config)).cached
    with duckdb.connect(str(config.resolved_database_path)) as db:
        assert db.execute("SELECT count(*) FROM assquack_meta.exports").fetchone() == (
            6,
        )
        db.execute(
            "DELETE FROM assquack_meta.exports WHERE resolved_uri = ?",
            [str(tmp_path / "b/orders.csv")],
        )
    result = asyncio.run(orders.cache_first()(assquack_config=config))
    assert result.cached and calls == 1
    with duckdb.connect(str(config.resolved_database_path)) as db:
        assert db.execute("SELECT count(*) FROM assquack_meta.exports").fetchone() == (
            6,
        )
    assert result.export_parquet("manual.parquet") == [
        str(tmp_path / name / "manual.parquet") for name in ("a", "b")
    ]


@pytest.mark.parametrize("file_uri", [False, True])
def test_duplicate_outputs_fail_before_extraction(
    tmp_path: Path, file_uri: bool
) -> None:
    @asset("orders.parquet", table="main.orders")
    def orders():
        pytest.fail("must reject overlapping destinations before extraction")

    config = AssquackConfig(
        home=tmp_path,
        exports=[
            FileSystemDestination(base_uri=str(tmp_path / "out")),
            FileSystemDestination(
                base_uri=(tmp_path / "out").as_uri()
                if file_uri
                else str(tmp_path / "out/../out"),
                auth="none",
            ),
        ],
    )
    with pytest.raises(ExportError, match="same write path"):
        asyncio.run(orders(assquack_config=config))
    assert not config.resolved_database_path.exists()


def test_config_normalizes_one_or_many_destinations_and_rejects_old_fields() -> None:
    destination = FileSystemDestination(base_uri="./out")
    assert AssquackConfig(exports=destination).exports == [destination]
    assert AssquackConfig(exports=[destination]).exports == [destination]
    for field in (
        "enabled",
        "options",
        "snapshot_runs",
        "writer",
        "credential_provider",
    ):
        with pytest.raises(ValidationError):
            FileSystemDestination.model_validate({"base_uri": "./out", field: True})
