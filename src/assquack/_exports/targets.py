from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from assquack._config import AssquackConfig
from assquack._errors import ExportError
from assquack._exports.models import ExportTarget


def parse_export_target(
    export: str | Mapping[str, str],
    *,
    asset_name: str,
    config: AssquackConfig,
) -> ExportTarget:
    if isinstance(export, Mapping):
        alias = export.get("uri")
        format_name = export.get("format", "parquet")
        if alias is None:
            raise ExportError("Structured export targets require a 'uri'.")
    else:
        alias = export
        format_name = "parquet"

    if format_name != "parquet":
        raise ExportError("The transient prototype only writes Parquet exports.")
    if "|" in alias:
        raise ExportError("Pipe-style multi-format exports are not in this prototype.")

    if alias == "parquet":
        relative_path = f"{asset_name}.parquet"
        logical_uri = f"mad://{relative_path}"
    elif alias.startswith("mad://"):
        relative_path = alias.removeprefix("mad://").lstrip("/")
        logical_uri = alias
    elif "://" in alias:
        return ExportTarget(alias=alias, logical_uri=alias, resolved_uri=alias)
    else:
        relative_path = alias
        logical_uri = f"mad://{alias}"

    if PurePosixPath(relative_path).suffix.lower() != ".parquet":
        relative_path = f"{relative_path}.parquet"
        logical_uri = f"mad://{relative_path}"

    base_uri = config.exports.base_uri or ""
    if "://" in base_uri:
        resolved = f"{base_uri.rstrip('/')}/{relative_path}"
    else:
        resolved = str(Path(base_uri) / Path(relative_path))
    return ExportTarget(alias=alias, logical_uri=logical_uri, resolved_uri=resolved)


def default_parquet_target(
    path: str | None,
    *,
    asset_name: str,
    config: AssquackConfig,
) -> ExportTarget:
    return parse_export_target(path or "parquet", asset_name=asset_name, config=config)
