from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import duckdb
import pytest

from assquack import AssquackConfig, asset
from assquack._materialization import commit, pipeline
from assquack._materialization.models import AssetMode


@pytest.mark.parametrize("explicit_mode", [False, True])
def test_declared_mode_selects_publication_and_persists_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, explicit_mode: bool
) -> None:
    rows = [{"id": 1}]
    source = Mock(side_effect=lambda: rows)

    def orders() -> list[dict[str, int]]:
        return source()

    declaration = (
        asset(table="main.orders", mode="replace")
        if explicit_mode
        else asset(table="main.orders")
    )
    orders_asset = declaration(orders).with_arguments()
    assert orders_asset._request((), {}).mode == orders_asset.mode

    strategy = Mock(wraps=commit.ReplaceCommitStrategy())
    factory = Mock(return_value=strategy)
    monkeypatch.setattr(commit, "_STRATEGIES", {"replace": factory})
    config = AssquackConfig(home=tmp_path)
    first = asyncio.run(orders_asset(assquack_config=config))
    rows[:] = [{"id": 2}]
    second = asyncio.run(orders_asset(assquack_config=config))
    cached = asyncio.run(orders_asset.cache_first()(assquack_config=config))

    assert factory.call_count == 3
    assert strategy.publish.call_count == 2
    assert source.call_count == 2
    assert cached.cached and cached.run_id == second.run_id
    assert first.run_id != second.run_id
    with duckdb.connect(str(config.resolved_database_path)) as connection:
        assert connection.execute("SELECT id FROM main.orders").fetchall() == [(2,)]
        assert connection.execute(
            "SELECT materialization_mode FROM assquack_meta.assets"
        ).fetchall() == [(orders_asset.mode,)]


@pytest.mark.parametrize("mode", ["append", "merge", "snapshot", "unknown"])
def test_unsupported_mode_is_rejected_before_source_or_storage(
    tmp_path: Path, mode: str
) -> None:
    invalid_mode = cast(AssetMode, mode)
    with pytest.raises(ValueError, match="only supports mode='replace'"):
        asset("main.orders", mode=invalid_mode)

    @asset(table="main.orders")
    def orders() -> list[dict[str, int]]:
        pytest.fail("Unsupported modes must not execute the source")

    config = AssquackConfig(home=tmp_path)
    request = replace(orders._request((), {}), mode=invalid_mode, use_cache=True)
    with pytest.raises(ValueError, match="Unsupported materialization mode"):
        asyncio.run(pipeline.materialize(request, config))
    assert not config.resolved_database_path.exists()
    assert not config.resolved_fragment_directory.exists()
