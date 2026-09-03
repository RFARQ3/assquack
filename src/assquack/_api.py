from __future__ import annotations

import hashlib
import inspect
import json
import warnings
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Generic, Literal, ParamSpec, TypeAlias, TypedDict, TypeVar

import duckdb

from assquack._cache import asset_exists
from assquack._config import AssquackConfig
from assquack._errors import MissingAssetSchemaWarning
from assquack._materialization.models import MaterializationRequest
from assquack._materialization.pipeline import materialize
from assquack._query import execute_query
from assquack._result import AssquackResult
from assquack._storage.database import open_database
from assquack._storage.metadata import MetadataRepository
from assquack._storage.sql import sanitize_identifier
from assquack._storage.tables import TableReference
from assquack._templates import AssetTemplateFormatter

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
class _AssetTemplates:
    export: str | ExportSpec | None
    name: str
    table: str | None


@dataclass(frozen=True, slots=True)
class AssquackAsset(Generic[P, AssetReturn]):
    """Callable asset definition whose runtime delegates to materialization."""

    fn: AssetFunction[P, AssetReturn]
    export: str | ExportSpec | None = None
    name: str | None = None
    table: str | None = None
    mode: AssetMode = "replace"
    bound_arguments: Mapping[str, Any] = field(default_factory=dict)
    prefer_cache: bool = False
    _templates: _AssetTemplates | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        templates = self._templates
        if templates is None:
            asset_name = self.name or self.fn.__name__
            templates = _AssetTemplates(
                export=_copy_export(self.export),
                name=asset_name,
                table=self.table,
            )
            object.__setattr__(self, "_templates", templates)

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
            formatter.format(templates.name, allow_partial=True),
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
        config = assquack_config or AssquackConfig()
        return await materialize(self._request(args, kwargs), config)

    async def query(
        self,
        sql: str | None = None,
        params: Sequence[object] | None = None,
        *,
        assquack_config: AssquackConfig | None = None,
    ) -> duckdb.DuckDBPyRelation:
        config = assquack_config or AssquackConfig()
        request = self._request((), {})
        return execute_query(config, request.table, sql, params)

    def exists(self, *, assquack_config: AssquackConfig | None = None) -> bool:
        config = assquack_config or AssquackConfig()
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

    def cache_first(self) -> AssquackAsset[P, AssetReturn]:
        return replace(self, prefer_cache=True)

    def with_arguments(self, **kwargs: Any) -> AssquackAsset[P, AssetReturn]:
        return replace(self, bound_arguments={**self.bound_arguments, **kwargs})

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
        asset_name = formatter.format(templates.name)
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
        table = _resolve_table(
            table_override,
            asset_name,
            asset_id,
            bool(bound.arguments),
        )

        return MaterializationRequest(
            fn=self.fn,
            arguments=bound.args,
            keyword_arguments=bound.kwargs,
            asset_id=asset_id,
            asset_name=asset_name,
            asset_signature=str(signature),
            table=table,
            export=export,
            use_cache=self.prefer_cache,
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


def _resolve_table(
    override: str | None,
    asset_name: str,
    asset_id: str,
    has_arguments: bool,
) -> TableReference:
    if override and "." in override:
        declared_schema, table_name = override.split(".", maxsplit=1)
        schema_name = sanitize_identifier(declared_schema, fallback="")
        if schema_name:
            return TableReference(schema_name, sanitize_identifier(table_name))

    warnings.warn(
        f"Asset {asset_name!r} does not define a DuckDB schema; using 'main'. "
        "Set table='schema.table' to define one explicitly.",
        MissingAssetSchemaWarning,
        stacklevel=4,
    )

    if override:
        table_name = sanitize_identifier(override)
    else:
        table_name = sanitize_identifier(asset_name)
        if has_arguments:
            table_name = f"{table_name}_{asset_id[:10]}"

    return TableReference("main", table_name)


def asset(
    export: str | ExportSpec | None = None,
    *,
    name: str | None = None,
    table: str | None = None,
    mode: AssetMode = "replace",
) -> Callable[[AssetFunction[P, AssetReturn]], AssquackAsset[P, AssetReturn]]:
    """Declare an Assquack asset without choosing storage placement."""

    if mode != "replace":
        raise ValueError("The Assquack prototype only supports mode='replace'.")

    def decorate(fn: AssetFunction[P, AssetReturn]) -> AssquackAsset[P, AssetReturn]:
        return AssquackAsset(
            fn=fn,
            export=export,
            name=name,
            table=table,
            mode=mode,
        )

    return decorate
