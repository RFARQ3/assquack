"""Public import surface for Assquack."""

import os as _os

from assquack._asset import AssquackAsset
from assquack._config import (
    AssquackConfig,
    DuckDBConfig,
    IngestionConfig,
    SchemaConfig,
)
from assquack._decorator import asset
from assquack._errors import MissingAssetSchemaWarning
from assquack._exports.destinations import ExportDestination, FileSystemDestination
from assquack._options import AssetMode, ExportFormat, ExportSpec
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

# Placement settings opt into import-time initialization. Resource and export
# settings alone do not request a disk database. No .env file is loaded.
if any(name in _os.environ for name in ("ASSQUACK_HOME", "ASSQUACK_DATABASE_PATH")):
    AssquackConfig().attach()
