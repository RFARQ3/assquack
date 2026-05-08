from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, Literal, ParamSpec, TypeAlias, TypedDict, TypeVar

AssetMode = Literal["replace"]
ExportFormat = Literal["parquet", "json", "csv"]


class ExportSpec(TypedDict):
    uri: str
    format: ExportFormat


P = ParamSpec("P")
AssetReturn = TypeVar("AssetReturn")
AssetFunction: TypeAlias = (
    Callable[P, AssetReturn] | Callable[P, Awaitable[AssetReturn]]
)


@dataclass(frozen=True, slots=True)
class AssquackAsset(Generic[P, AssetReturn]):
    """Bootstrap asset definition returned by the public decorator."""

    fn: AssetFunction[P, AssetReturn]
    export: str | ExportSpec | None = None
    name: str | None = None
    table: str | None = None
    mode: AssetMode = "replace"


def asset(
    export: str | ExportSpec | None = None,
    *,
    name: str | None = None,
    table: str | None = None,
    mode: AssetMode = "replace",
) -> Callable[[AssetFunction[P, AssetReturn]], AssquackAsset[P, AssetReturn]]:
    """Declare an Assquack asset without choosing storage placement."""

    if mode != "replace":
        raise ValueError("Assquack MVP bootstrap only supports mode='replace'.")

    def decorate(fn: AssetFunction[P, AssetReturn]) -> AssquackAsset[P, AssetReturn]:
        return AssquackAsset(
            fn=fn,
            export=export,
            name=name,
            table=table,
            mode=mode,
        )

    return decorate
