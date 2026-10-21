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
    ExportsConfig,
    IngestionConfig,
    SchemaConfig,
)
from assquack._errors import MissingAssetSchemaWarning
from assquack._result import AssquackResult

__all__ = [
    "AssetMode",
    "AssquackAsset",
    "AssquackConfig",
    "AssquackResult",
    "DuckDBConfig",
    "ExportFormat",
    "ExportSpec",
    "ExportsConfig",
    "IngestionConfig",
    "MissingAssetSchemaWarning",
    "SchemaConfig",
    "asset",
]
