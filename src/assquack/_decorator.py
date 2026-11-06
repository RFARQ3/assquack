from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from assquack._asset import AssetFunction, AssetReturn, AssquackAsset, P
from assquack._config import AssquackConfig
from assquack._options import AssetMode, ExportSpec, validate_options


def asset(
    table: str,
    *,
    mode: AssetMode = "replace",
    name: str | None = None,
    export: str | ExportSpec | None = None,
    cache_expiration: timedelta | None = None,
    config: AssquackConfig | None = None,
) -> Callable[[AssetFunction[P, AssetReturn]], AssquackAsset[P, AssetReturn]]:
    """Declare an Assquack asset without choosing storage placement."""

    validate_options(table, mode, cache_expiration)

    def decorate(fn: AssetFunction[P, AssetReturn]) -> AssquackAsset[P, AssetReturn]:
        return AssquackAsset(
            fn=fn,
            export=export,
            name=name,
            table=table,
            mode=mode,
            prefer_cache=cache_expiration is not None
            and cache_expiration > timedelta(0),
            cache_expiration=cache_expiration,
            config=config,
        )

    return decorate
