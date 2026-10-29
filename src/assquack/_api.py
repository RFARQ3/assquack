from __future__ import annotations

import hashlib
import inspect
import json
import warnings
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import timedelta
from enum import Enum
from typing import Any, Generic, Literal, ParamSpec, TypeAlias, TypedDict, TypeVar

import duckdb

from assquack._cache import asset_exists
from assquack._config import AssquackConfig
from assquack._errors import MissingAssetSchemaWarning
from assquack._materialization.models import AssetMode as AssetMode
from assquack._materialization.models import MaterializationRequest
from assquack._materialization.pipeline import materialize
from assquack._query import execute_query
from assquack._result import AssquackResult
from assquack._storage.database import open_database
from assquack._storage.metadata import MetadataRepository
from assquack._storage.sql import sanitize_identifier
from assquack._storage.tables import TableReference
from assquack._templates import AssetTemplateFormatter

ExportFormat = Literal["parquet", "json", "csv"]


class ExportSpec(TypedDict):
    uri: str
    format: ExportFormat


P = ParamSpec("P")
AssetReturn = TypeVar("AssetReturn")


class _Unset(Enum):
    VALUE = "unset"


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
    bound_arguments: Mapping[str, Any] = field(default_factory=dict)
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

        _validate_options(templates.table, self.mode, self.cache_expiration)
        bound = self._get_bound_arguments()
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

    async def __call__(
        self,
        *args: P.args,
        assquack_config: AssquackConfig | None = None,
        **kwargs: P.kwargs,
    ) -> AssquackResult:
        config = assquack_config or self.config or AssquackConfig()
        return await materialize(self._request(args, kwargs), config)

    async def query(
        self,
        sql: str | None = None,
        params: Sequence[object] | None = None,
        *,
        assquack_config: AssquackConfig | None = None,
    ) -> duckdb.DuckDBPyRelation:
        config = assquack_config or self.config or AssquackConfig()
        request = self._request((), {})
        return execute_query(config, request.table, sql, params)

    def exists(self, *, assquack_config: AssquackConfig | None = None) -> bool:
        config = assquack_config or self.config or AssquackConfig()
        request = self._request((), {})
        connection = open_database(config)
        try:
            return asset_exists(
                connection,
                MetadataRepository(connection),
                asset_id=request.asset_id,
                table=request.table,
            )
        finally:
            connection.close()

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
        table: str | _Unset = _Unset.VALUE,
        mode: AssetMode | _Unset = _Unset.VALUE,
        name: str | None | _Unset = _Unset.VALUE,
        export: str | ExportSpec | None | _Unset = _Unset.VALUE,
        cache_expiration: timedelta | None | _Unset = _Unset.VALUE,
        config: AssquackConfig | None | _Unset = _Unset.VALUE,
    ) -> AssquackAsset[P, AssetReturn]:
        """Copy declared options, then resolve templates against retained arguments."""
        templates = self._templates
        assert templates is not None

        updated_templates = _AssetTemplates(
            table=templates.table if isinstance(table, _Unset) else table,
            name=templates.name if isinstance(name, _Unset) else name,
            export=_copy_export(
                templates.export if isinstance(export, _Unset) else export
            ),
        )
        expiration = (
            self.cache_expiration
            if isinstance(cache_expiration, _Unset)
            else cache_expiration
        )
        # An omitted TTL preserves fluent unlimited caching. An explicit reset
        # returns to the decorator's ordinary uncached-call behavior.
        prefer_cache = (
            self.prefer_cache
            if isinstance(cache_expiration, _Unset)
            else expiration is not None and expiration > timedelta(0)
        )
        return replace(
            self,
            table=updated_templates.table,
            mode=self.mode if isinstance(mode, _Unset) else mode,
            cache_expiration=expiration,
            prefer_cache=prefer_cache,
            config=self.config if isinstance(config, _Unset) else config,
            _templates=updated_templates,
        )

    def _get_bound_arguments(self) -> inspect.BoundArguments:
        signature = inspect.signature(self.fn)
        bound = signature.bind_partial(**self.bound_arguments)
        bound.apply_defaults()
        return bound

    def _request(
        self,
        args: tuple[Any, ...],
        kwargs: Mapping[str, Any],
    ) -> MaterializationRequest:
        call_kwargs = {**self.bound_arguments, **kwargs}
        signature = inspect.signature(self.fn)
        bound = signature.bind(*args, **call_kwargs)
        bound.apply_defaults()

        templates = self._templates
        assert templates is not None

        formatter = AssetTemplateFormatter(bound.args, bound)
        asset_name = formatter.format(templates.name or templates.table)
        table_override = formatter.format(templates.table)
        export = _format_export(templates.export, formatter)

        identity_payload = {
            "function": f"{self.fn.__module__}.{self.fn.__qualname__}",
            "name": asset_name,
            "arguments": bound.arguments,
        }
        canonical_identity = json.dumps(
            identity_payload,
            default=repr,
            sort_keys=True,
            separators=(",", ":"),
        )
        asset_id = hashlib.sha256(canonical_identity.encode()).hexdigest()
        table = _resolve_table(table_override, asset_name)

        return MaterializationRequest(
            fn=self.fn,
            arguments=bound.args,
            keyword_arguments=bound.kwargs,
            asset_id=asset_id,
            asset_name=asset_name,
            asset_signature=str(signature),
            table=table,
            export=export,
            mode=self.mode,
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


def _validate_table(table: object) -> None:
    if not isinstance(table, str):
        raise TypeError("Asset table must be a non-empty string.")
    if not table.strip():
        raise ValueError("Asset table must be a non-empty string.")
    if any(not sanitize_identifier(part, fallback="") for part in table.split(".")):
        raise ValueError("Asset table must contain a non-empty schema/table name.")


def _resolve_table(table: str, asset_name: str) -> TableReference:
    _validate_table(table)
    if "." in table:
        declared_schema, table_name = table.split(".", maxsplit=1)
        return TableReference(
            sanitize_identifier(declared_schema), sanitize_identifier(table_name)
        )

    warnings.warn(
        f"Asset {asset_name!r} does not define a DuckDB schema; using 'main'. "
        "Set table='schema.table' to define one explicitly.",
        MissingAssetSchemaWarning,
        stacklevel=4,
    )
    return TableReference("main", sanitize_identifier(table))


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

    _validate_options(table, mode, cache_expiration)

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


def _validate_options(
    table: str, mode: AssetMode, cache_expiration: timedelta | None
) -> None:
    _validate_table(table)
    if cache_expiration is not None and cache_expiration < timedelta(0):
        raise ValueError("Cache expiration cannot be negative.")
    if mode != "replace":
        raise ValueError("The Assquack prototype only supports mode='replace'.")
