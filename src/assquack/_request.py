from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

from assquack._config import AssquackConfig
from assquack._options import AssetMode, ExportSpec


def copy_arguments(value: Any) -> Any:
    """Copy ordinary data containers while retaining live runtime objects.

    Generators, assets and database relations must remain usable by their caller.
    They are execution inputs, not data that can be frozen by copying it.
    """
    memo: dict[int, Any] = {}
    visited: set[int] = set()

    def retain_resources(item: Any) -> None:
        if id(item) in visited:
            return
        visited.add(id(item))
        if type(item) is dict:
            for key, child in cast(dict[Any, Any], item).items():
                retain_resources(key)
                retain_resources(child)
        elif type(item) in (list, tuple, set, frozenset):
            for child in item:
                retain_resources(child)
        elif type(item) not in (str, bytes, bytearray, int, float, bool, type(None)):
            memo[id(item)] = item

    retain_resources(value)
    return deepcopy(value, memo)


@dataclass(frozen=True, slots=True)
class AssquackRequest:
    """Preserve the arguments and declared options for one asset call.

    Argument access returns fresh containers so producer mutations cannot rewrite
    the values used to identify and resolve this call. Live runtime objects retain
    their identity and are not copied.
    """

    fn: Callable[..., Any]
    _arguments: tuple[Any, ...]
    _keyword_arguments: Mapping[str, Any]
    table_template: str
    name_template: str | None
    _export_template: str | ExportSpec | None
    mode: AssetMode
    config: AssquackConfig
    use_cache: bool = False
    cache_expiration: timedelta | None = None

    @classmethod
    def capture(
        cls,
        fn: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: Mapping[str, Any],
        *,
        table: str,
        name: str | None,
        export: str | ExportSpec | None,
        mode: AssetMode,
        config: AssquackConfig,
        use_cache: bool,
        cache_expiration: timedelta | None,
    ) -> AssquackRequest:
        bound = inspect.signature(fn).bind(*args, **kwargs)
        bound.apply_defaults()
        arguments, keyword_arguments = copy_arguments((bound.args, bound.kwargs))
        return cls(
            fn,
            arguments,
            keyword_arguments,
            table,
            name,
            copy_arguments(export),
            mode,
            capture_config(config),
            use_cache,
            cache_expiration,
        )

    @property
    def arguments(self) -> tuple[Any, ...]:
        return self.execution_arguments()[0]

    @property
    def keyword_arguments(self) -> Mapping[str, Any]:
        return self.execution_arguments()[1]

    @property
    def export_template(self) -> str | ExportSpec | None:
        return copy_arguments(self._export_template)

    def execution_arguments(self) -> tuple[tuple[Any, ...], dict[str, Any]]:
        """Give this execution its own containers, retaining shared argument aliases."""
        return copy_arguments((self._arguments, self._keyword_arguments))


def capture_config(config: AssquackConfig) -> AssquackConfig:
    """Copy settings while retaining destination providers and other live resources."""
    destinations = [
        destination.model_copy(
            update={
                name: copy_arguments(getattr(destination, name))
                for name in type(destination).model_fields
            }
        )
        for destination in config.export_destinations
    ]
    return config.model_copy(
        update={
            "duckdb": config.duckdb.model_copy(deep=True),
            "ingestion": config.ingestion.model_copy(deep=True),
            "schema_settings": config.schema.model_copy(deep=True),
            "exports": destinations,
        }
    )
