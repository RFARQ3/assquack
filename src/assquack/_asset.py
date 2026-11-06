from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any, Generic, ParamSpec, TypeAlias, TypeVar

import duckdb

from assquack._config import AssquackConfig
from assquack._options import AssetMode as AssetMode
from assquack._options import ExportSpec, Unset, validate_options
from assquack._pipeline import run_pipeline
from assquack._pipeline.resolve import resolve_request
from assquack._request import AssquackRequest
from assquack._result import AssquackResult
from assquack._storage.cache import asset_exists
from assquack._storage.database import database_catalog, database_operation
from assquack._storage.metadata import MetadataRepository
from assquack._templates import AssetTemplateFormatter

P = ParamSpec("P")
AssetReturn = TypeVar("AssetReturn")
AssetFunction: TypeAlias = (
    Callable[P, AssetReturn] | Callable[P, Awaitable[AssetReturn]]
)


@dataclass(frozen=True, slots=True)
class _AssetTemplates:
    export: str | ExportSpec | None
    name: str | None
    table: str


@dataclass(frozen=True, slots=True)
class AssquackAsset(Generic[P, AssetReturn]):
    """Callable asset definition whose runtime delegates to materialization."""

    fn: AssetFunction[P, AssetReturn]
    table: str
    export: str | ExportSpec | None = None
    name: str | None = None
    mode: AssetMode = "replace"
    bound_arguments: Mapping[str, Any] = field(default_factory=dict[str, Any])
    prefer_cache: bool = False
    cache_expiration: timedelta | None = None
    config: AssquackConfig | None = None
    _templates: _AssetTemplates | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        templates = self._templates
        if templates is None:
            templates = _AssetTemplates(
                export=_copy_export(self.export),
                name=self.name,
                table=self.table,
            )
            object.__setattr__(self, "_templates", templates)

        validate_options(templates.table, self.mode, self.cache_expiration)
        bound = self.get_bound_arguments()
        formatter = AssetTemplateFormatter(bound.args, bound)

        object.__setattr__(
            self,
            "export",
            _format_export(templates.export, formatter, allow_partial=True),
        )
        object.__setattr__(
            self,
            "name",
            formatter.format(templates.name or templates.table, allow_partial=True),
        )
        object.__setattr__(
            self,
            "table",
            formatter.format(templates.table, allow_partial=True),
        )

    def __call__(
        self,
        *args: P.args,
        assquack_config: AssquackConfig | None = None,  # pyright: ignore[reportGeneralTypeIssues]
        **kwargs: P.kwargs,
    ) -> Coroutine[Any, Any, AssquackResult]:
        """Capture this call now; execute it when the returned coroutine is awaited."""
        request = self._request(args, kwargs, config=assquack_config)
        return run_pipeline(request)

    async def query(
        self,
        sql: str | None = None,
        params: Sequence[object] | Mapping[str, object] | None = None,
        *,
        assquack_config: AssquackConfig | None = None,
    ) -> duckdb.DuckDBPyRelation:
        result = await self(assquack_config=assquack_config)
        return await result.query(sql, params)

    def exists(self, *, assquack_config: AssquackConfig | None = None) -> bool:
        captured = self._request((), {}, config=assquack_config)
        config = captured.config
        request = resolve_request(captured)
        with database_operation(config) as connection:
            return asset_exists(
                connection,
                MetadataRepository(connection, database_catalog(config)),
                asset_id=request.asset_id,
                table=replace(request.table, catalog_name=database_catalog(config)),
            )

    def cache_first(
        self,
        expiration: timedelta | None = None,
    ) -> AssquackAsset[P, AssetReturn]:
        if expiration is not None and expiration < timedelta(0):
            raise ValueError("Cache expiration cannot be negative.")
        return replace(
            self,
            prefer_cache=True,
            cache_expiration=expiration,
        )

    def with_arguments(self, **kwargs: Any) -> AssquackAsset[P, AssetReturn]:
        return replace(self, bound_arguments={**self.bound_arguments, **kwargs})

    def with_options(
        self,
        *,
        table: str | Unset = Unset.VALUE,
        mode: AssetMode | Unset = Unset.VALUE,
        name: str | None | Unset = Unset.VALUE,
        export: str | ExportSpec | None | Unset = Unset.VALUE,
        cache_expiration: timedelta | None | Unset = Unset.VALUE,
        config: AssquackConfig | None | Unset = Unset.VALUE,
    ) -> AssquackAsset[P, AssetReturn]:
        """Copy declared options, then resolve templates against retained arguments."""
        templates = self._templates
        assert templates is not None

        updated_templates = _AssetTemplates(
            table=templates.table if isinstance(table, Unset) else table,
            name=templates.name if isinstance(name, Unset) else name,
            export=_copy_export(
                templates.export if isinstance(export, Unset) else export
            ),
        )
        expiration = (
            self.cache_expiration
            if isinstance(cache_expiration, Unset)
            else cache_expiration
        )
        # An omitted TTL preserves fluent unlimited caching. An explicit reset
        # returns to the decorator's ordinary uncached-call behavior.
        prefer_cache = (
            self.prefer_cache
            if isinstance(cache_expiration, Unset)
            else expiration is not None and expiration > timedelta(0)
        )
        return replace(
            self,
            table=updated_templates.table,
            mode=self.mode if isinstance(mode, Unset) else mode,
            cache_expiration=expiration,
            prefer_cache=prefer_cache,
            config=self.config if isinstance(config, Unset) else config,
            _templates=updated_templates,
        )

    def get_bound_arguments(self) -> inspect.BoundArguments:
        signature = inspect.signature(self.fn)
        bound = signature.bind_partial(**self.bound_arguments)
        bound.apply_defaults()
        return bound

    def _request(
        self,
        args: tuple[Any, ...],
        kwargs: Mapping[str, Any],
        *,
        config: AssquackConfig | None = None,
    ) -> AssquackRequest:
        """Capture the call before resolution or producer execution can change it."""
        assert self._templates is not None
        return AssquackRequest.capture(
            self.fn,
            args,
            {**self.bound_arguments, **kwargs},
            table=self._templates.table,
            name=self._templates.name,
            export=self._templates.export,
            mode=self.mode,
            config=config or self.config or AssquackConfig(),
            use_cache=self.prefer_cache,
            cache_expiration=self.cache_expiration,
        )


def _copy_export(export: str | ExportSpec | None) -> str | ExportSpec | None:
    if not isinstance(export, dict):
        return export

    return ExportSpec(uri=export["uri"], format=export["format"])


def _format_export(
    export: str | ExportSpec | None,
    formatter: AssetTemplateFormatter,
    *,
    allow_partial: bool = False,
) -> str | ExportSpec | None:
    if isinstance(export, str):
        return formatter.format(export, allow_partial=allow_partial)
    if export is None:
        return None

    return ExportSpec(
        uri=formatter.format(export["uri"], allow_partial=allow_partial),
        format=export["format"],
    )
