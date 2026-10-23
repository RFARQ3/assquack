from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from assquack._config import AssquackConfig
from assquack._errors import ExportError
from assquack._exports.models import ExportTarget

_FORMAT_OPTIONS: dict[str, dict[str, object]] = {
    "parquet": {},
    "csv": {"header": True},
    "json": {"array": False},
}
EXPORT_FORMATS = frozenset(_FORMAT_OPTIONS)


def parse_export_targets(
    export: str | Mapping[str, str],
    *,
    asset_name: str,
    config: AssquackConfig,
) -> tuple[ExportTarget, ...]:
    """Expand one declaration, retaining its first format as the default output."""
    if config.exports.options:
        raise ExportError("Custom export options are not implemented.")
    explicit_format = None
    if isinstance(export, Mapping):
        alias = export.get("uri")
        explicit_format = export.get("format")
        if alias is None:
            raise ExportError("Structured export targets require a 'uri'.")
        if explicit_format is not None and explicit_format not in EXPORT_FORMATS:
            raise ExportError("Unsupported export format.")
    else:
        alias = export

    parts = alias.split("|")
    if any(not part or part != part.strip() for part in parts):
        raise ExportError("Export aliases require nonempty paths and format entries.")
    first, *additional = parts
    if any(part not in EXPORT_FORMATS for part in additional):
        raise ExportError("Pipe entries must be format names, not additional paths.")
    if explicit_format is not None and additional:
        raise ExportError("An explicit format cannot be combined with pipe syntax.")

    if first.startswith("mad://"):
        suffix = PurePosixPath(first.removeprefix("mad://")).suffix.lower()
    elif "://" in first:
        parsed = urlsplit(first)
        if parsed.query or parsed.fragment:
            raise ExportError("Export URIs cannot contain query strings or fragments.")
        suffix = PurePosixPath(parsed.path).suffix.lower()
    else:
        suffix = PurePosixPath(first).suffix.lower()

    if first in EXPORT_FORMATS:
        selected_format = first
        stem = asset_name
    elif suffix:
        selected_format = suffix[1:]
        if selected_format not in EXPORT_FORMATS:
            raise ExportError(f"Unsupported export suffix: {suffix!r}.")
        stem = first[: -len(suffix)]
    else:
        selected_format = explicit_format or config.exports.default_format
        stem = first
    if explicit_format is not None and explicit_format != selected_format:
        raise ExportError("Explicit export format conflicts with the alias or suffix.")

    formats = [selected_format, *additional]
    if len(set(formats)) != len(formats):
        raise ExportError("An export declaration cannot repeat a format.")

    targets: list[ExportTarget] = []
    for index, format_name in enumerate(formats):
        uri = f"{stem}.{format_name}"
        role = "default" if index == 0 else "legacy"
        options = _FORMAT_OPTIONS[format_name].copy()
        if uri.startswith("mad://"):
            relative_path = uri.removeprefix("mad://").lstrip("/")
        elif "://" in uri:
            targets.append(ExportTarget(alias, uri, uri, format_name, role, options))
            continue
        else:
            relative_path = uri

        base_uri = config.exports.base_uri or ""
        resolved = (
            f"{base_uri.rstrip('/')}/{relative_path}"
            if "://" in base_uri
            else str(Path(base_uri) / Path(relative_path))
        )
        targets.append(
            ExportTarget(
                alias, f"mad://{relative_path}", resolved, format_name, role, options
            )
        )
    return tuple(targets)


def parse_export_target(
    export: str | Mapping[str, str],
    *,
    asset_name: str,
    config: AssquackConfig,
) -> ExportTarget:
    targets = parse_export_targets(export, asset_name=asset_name, config=config)
    if len(targets) != 1:
        raise ExportError("This operation requires a single export target.")
    return targets[0]


def default_parquet_target(
    path: str | None,
    *,
    asset_name: str,
    config: AssquackConfig,
) -> ExportTarget:
    return parse_export_target(
        {"uri": path or "parquet", "format": "parquet"},
        asset_name=asset_name,
        config=config,
    )
