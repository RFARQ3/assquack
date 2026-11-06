from __future__ import annotations

import asyncio
import hashlib
import importlib
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import duckdb
import pytest

from assquack import AssquackConfig, asset
from assquack._storage.fragment_writer import fragment_run_directory
from assquack._storage.tables import TableReference
from database_helpers import inspect_database

run_module = importlib.import_module("assquack._run")


@pytest.fixture
def run_clock(monkeypatch: pytest.MonkeyPatch) -> Mock:
    clock = Mock()
    clock.now.return_value = datetime(2026, 10, 22, 1, 2, 3, 456789, tzinfo=UTC)
    monkeypatch.setattr(run_module, "datetime", clock)
    return clock


def test_run_identity_matches_known_md5_digest(tmp_path: Path, run_clock: Mock) -> None:
    from assquack._pipeline.resolve import ResolvedAsset
    from assquack._run import AssquackRun

    @asset("main.orders")
    def orders():
        return [{"id": 1}]

    run = AssquackRun(orders._request((), {}, config=AssquackConfig(home=tmp_path)))
    run.asset = ResolvedAsset(
        "asset-123",
        "orders",
        "orders()",
        TableReference("main", "orders"),
        None,
        "replace",
        False,
        None,
    )
    run.identify()
    # MD5 of UTF-8 "asset-123:2026-10-22T01:02:03.456789+00:00".
    assert run.id == "2b812ea6c821142e72d6945037880048"


def test_run_identity_matches_persisted_runtime_and_fragments(
    tmp_path: Path, run_clock: Mock
) -> None:
    @asset(table="main.orders")
    async def orders() -> list[dict[str, int]]:
        return [{"id": 1}]

    config = AssquackConfig(
        home=tmp_path / "first", attachment_name=(tmp_path / "first").name
    )
    first = asyncio.run(orders(assquack_config=config))
    repeated = asyncio.run(
        orders(
            assquack_config=AssquackConfig(
                home=tmp_path / "repeat", attachment_name=(tmp_path / "repeat").name
            )
        )
    )
    assert repeated.run_id == first.run_id
    assert (
        first.run_id
        == hashlib.md5(
            f"{first.asset_id}:2026-10-22T01:02:03.456789+00:00".encode()
        ).hexdigest()
    )
    assert re.fullmatch(r"[0-9a-f]{32}", first.run_id)
    run_clock.now.assert_called_with(UTC)

    with inspect_database(config) as connection:
        assert connection.execute(
            "SELECT run_id, asset_id, runtime = ? FROM assquack_meta.runs",
            [run_clock.now.return_value],
        ).fetchall() == [(first.run_id, first.asset_id, True)]
        assert connection.execute("SELECT _qa_run_id FROM main.orders").fetchall() == [
            (first.run_id,)
        ]

    directory = fragment_run_directory(
        config.resolved_fragment_directory, "main.orders", first.run_id
    )
    assert (directory / "_COMPLETE").exists()
    assert (directory / "fragment=000000.ndjson").read_text() == '{"id":1}\n'

    run_clock.now.return_value += timedelta(microseconds=1)
    later = asyncio.run(orders(assquack_config=config))
    assert (
        later.run_id
        == hashlib.md5(
            f"{first.asset_id}:2026-10-22T01:02:03.456790+00:00".encode()
        ).hexdigest()
    )
    assert later.run_id != first.run_id

    @asset(table="main.other_orders")
    async def other_orders() -> list[dict[str, int]]:
        return [{"id": 1}]

    other = asyncio.run(other_orders(assquack_config=config))
    assert other.asset_id != later.asset_id
    assert other.run_id != later.run_id


@pytest.mark.parametrize("failed", [False, True])
def test_duplicate_identity_preserves_existing_run(
    tmp_path: Path, run_clock: Mock, failed: bool
) -> None:
    calls = 0

    @asset(table="main.orders")
    async def orders() -> list[dict[str, int]]:
        nonlocal calls
        calls += 1
        if failed:
            raise RuntimeError("synthetic failure")
        return [{"id": calls}]

    config = AssquackConfig(home=tmp_path)
    if failed:
        with pytest.raises(RuntimeError, match="synthetic failure"):
            asyncio.run(orders(assquack_config=config))
    else:
        asyncio.run(orders(assquack_config=config))

    with inspect_database(config) as connection:
        original = connection.execute(
            "SELECT run_id, to_json(r) FROM assquack_meta.runs AS r"
        ).fetchall()
    files = {
        path: path.read_bytes()
        for path in config.resolved_fragment_directory.rglob("*")
        if path.is_file()
    }
    with pytest.raises(duckdb.ConstraintException):
        asyncio.run(orders(assquack_config=config))
    assert calls == 1
    assert files == {
        path: path.read_bytes()
        for path in config.resolved_fragment_directory.rglob("*")
        if path.is_file()
    }
    with inspect_database(config) as connection:
        assert (
            connection.execute(
                "SELECT run_id, to_json(r) FROM assquack_meta.runs AS r"
            ).fetchall()
            == original
        )
        if not failed:
            assert connection.execute("SELECT id FROM main.orders").fetchall() == [(1,)]

    if not failed:
        cached = asyncio.run(orders.cache_first()(assquack_config=config))
        assert cached.cached
        assert cached.run_id == original[0][0]
        assert calls == 1

    run_clock.now.return_value += timedelta(microseconds=1)
    failed = False
    recovered = asyncio.run(orders(assquack_config=config))
    assert recovered.run_id != original[0][0]
    assert calls == 2


def test_existing_fragment_directory_is_never_overwritten(
    tmp_path: Path, run_clock: Mock
) -> None:
    calls = 0

    @asset(table="main.orders")
    async def orders() -> list[dict[str, int]]:
        nonlocal calls
        calls += 1
        return [{"id": calls}]

    config = AssquackConfig(home=tmp_path)
    first = asyncio.run(orders(assquack_config=config))
    run_clock.now.return_value += timedelta(microseconds=1)
    orphan_id = hashlib.md5(
        f"{first.asset_id}:2026-10-22T01:02:03.456790+00:00".encode()
    ).hexdigest()
    directory = fragment_run_directory(
        config.resolved_fragment_directory, "main.orders", orphan_id
    )
    directory.mkdir(parents=True)
    fragment = directory / "fragment=000000.ndjson"
    fragment.write_bytes(b"preserve this evidence\n")

    with pytest.raises(FileExistsError):
        asyncio.run(orders(assquack_config=config))
    assert calls == 1
    assert fragment.read_bytes() == b"preserve this evidence\n"
    assert list(directory.iterdir()) == [fragment]
    with inspect_database(config) as connection:
        assert connection.execute(
            "SELECT status, runtime = ? FROM assquack_meta.runs WHERE run_id = ?",
            [run_clock.now.return_value, orphan_id],
        ).fetchone() == ("failed", True)
        assert connection.execute(
            "SELECT id, _qa_run_id FROM main.orders"
        ).fetchall() == [(1, first.run_id)]
