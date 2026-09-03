"""Shared Assquack exceptions."""


class AssquackError(Exception):
    """Base exception for prototype runtime failures."""


class ConfigurationError(AssquackError):
    """Raised when runtime configuration cannot be used."""


class MaterializationError(AssquackError):
    """Raised when an asset value cannot be materialized."""


class MissingAssetTableError(AssquackError):
    """Raised when a query has no successfully published table."""


class ExportError(AssquackError):
    """Raised when an export target is unsupported or cannot be written."""


class MissingAssetSchemaWarning(UserWarning):
    """Warn that an asset table is falling back to DuckDB's main schema."""
