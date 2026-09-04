from __future__ import annotations

import asyncio
import multiprocessing
from pathlib import Path

from assquack import AssquackConfig, asset
from assquack._storage.locking import writer_lock


def test_async_materializations_are_serialized(tmp_path: Path) -> None:
    active_calls = 0
    maximum_active_calls = 0

    async def source(value: int) -> list[dict[str, int]]:
        nonlocal active_calls, maximum_active_calls
        active_calls += 1
        maximum_active_calls = max(maximum_active_calls, active_calls)
        await asyncio.sleep(0.05)
        active_calls -= 1
        return [{"id": value}]

    first = asset(table="main.first")(source)
    second = asset(table="main.second")(source)
    config = AssquackConfig(home=tmp_path)

    async def materialize_both() -> None:
        await asyncio.gather(
            first(1, assquack_config=config),
            second(2, assquack_config=config),
        )

    asyncio.run(materialize_both())

    assert maximum_active_calls == 1


def test_separate_processes_are_serialized(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    database_path = tmp_path / "shared.duckdb"
    first_entered = context.Event()
    first_release = context.Event()
    second_entered = context.Event()
    second_release = context.Event()
    first = context.Process(
        target=_hold_writer_lock,
        args=(database_path, first_entered, first_release),
    )
    second = context.Process(
        target=_hold_writer_lock,
        args=(database_path, second_entered, second_release),
    )

    try:
        first.start()
        assert first_entered.wait(5)

        second.start()
        assert not second_entered.wait(0.25)

        first_release.set()
        assert second_entered.wait(5)
    finally:
        first_release.set()
        second_release.set()
        first.join(5)
        second.join(5)
        if first.is_alive():
            first.terminate()
            first.join(5)
        if second.is_alive():
            second.terminate()
            second.join(5)

    assert first.exitcode == 0
    assert second.exitcode == 0


def _hold_writer_lock(database_path: Path, entered, release) -> None:
    with writer_lock(database_path):
        entered.set()
        release.wait(5)
