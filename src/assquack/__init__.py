"""Public import surface for Assquack."""

from assquack._api import (
    AssetMode,
    AssquackAsset,
    ExportFormat,
    ExportSpec,
    asset,
)
from assquack._config import (
    AssquackConfig,
    DuckDBConfig,
    IngestionConfig,
    SchemaConfig,
)
from assquack._errors import MissingAssetSchemaWarning
from assquack._exports.destinations import ExportDestination, FileSystemDestination
from assquack._result import AssquackResult

__all__ = [
    "AssetMode",
    "AssquackAsset",
    "AssquackConfig",
    "AssquackResult",
    "DuckDBConfig",
    "ExportDestination",
    "ExportFormat",
    "ExportSpec",
    "FileSystemDestination",
    "IngestionConfig",
    "MissingAssetSchemaWarning",
    "SchemaConfig",
    "asset",
]
