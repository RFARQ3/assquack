from __future__ import annotations

from pathlib import Path
from typing import Literal, Self, cast

from pydantic import (
    AliasChoices,
    Field,
    SerializeAsAny,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from assquack._exports.destinations import ExportDestination, FileSystemDestination


class _ProcessSettings(BaseSettings):
    """Load typed settings from process environment variables only."""

    model_config = SettingsConfigDict(
        env_file=None,
        populate_by_name=True,
    )


class DuckDBConfig(_ProcessSettings):
    """Settings applied when Assquack opens its DuckDB database."""

    threads: int = Field(
        default=4,
        ge=1,
        validation_alias="ASSQUACK_DUCKDB__THREADS",
    )
    memory_limit: str = Field(
        default="8GB",
        validation_alias="ASSQUACK_DUCKDB__MEMORY_LIMIT",
    )
    temp_directory: Path | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ASSQUACK_DUCKDB__TEMP_DIRECTORY",
            "DUCKDB_TEMP_DIRECTORY",
        ),
    )
    extensions: tuple[str, ...] = Field(
        default=(),
        validation_alias="ASSQUACK_DUCKDB__EXTENSIONS",
    )


class IngestionConfig(_ProcessSettings):
    """Local NDJSON fragment settings used while materializing an asset."""

    fragment_directory: Path | None = Field(
        default=None,
        validation_alias="ASSQUACK_INGESTION__FRAGMENT_DIRECTORY",
    )
    fragment_target_mb: float = Field(
        default=64.0,
        gt=0,
        allow_inf_nan=False,
        description="Target NDJSON size in decimal MB (1,000,000 bytes).",
        validation_alias="ASSQUACK_INGESTION__FRAGMENT_TARGET_MB",
    )


class SchemaConfig(_ProcessSettings):
    """Select the schema policy used by replace materialization."""

    replace: Literal["current_run"] = Field(
        default="current_run",
        validation_alias="ASSQUACK_SCHEMA__REPLACE",
    )


class AssquackConfig(_ProcessSettings):
    """Authoritative runtime configuration accepted by the core package."""

    home: Path = Field(
        default_factory=Path.cwd,
        validation_alias="ASSQUACK_HOME",
    )
    database_path: Path | None = Field(
        default=None,
        validation_alias="ASSQUACK_DATABASE_PATH",
    )
    attachment_name: str = Field(
        default="assquack",
        min_length=1,
        validation_alias="ASSQUACK_ATTACHMENT_NAME",
    )
    duckdb: DuckDBConfig = Field(default_factory=DuckDBConfig)
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
    schema_settings: SchemaConfig = Field(default_factory=SchemaConfig, alias="schema")
    exports: (
        str
        | SerializeAsAny[FileSystemDestination | ExportDestination]
        | list[SerializeAsAny[FileSystemDestination | ExportDestination]]
    ) = Field(
        default_factory=list[ExportDestination], validation_alias="ASSQUACK_EXPORTS"
    )

    @field_validator("attachment_name")
    @classmethod
    def validate_attachment_name(cls, value: str) -> str:
        if not value.strip() or "\0" in value:
            raise ValueError("attachment_name must be a nonblank SQL catalog name.")
        return value

    def attach(self) -> None:
        """Attach this destination to DuckDB's default connection for SQL use."""
        from assquack._storage.database import connect_database

        connect_database(self)

    @field_validator("exports", mode="before")
    @classmethod
    def normalize_exports(cls, value: object) -> list[ExportDestination]:
        if isinstance(value, str):
            return [FileSystemDestination(base_uri=value)]
        destinations = cast(list[object], value) if isinstance(value, list) else [value]
        return [
            item
            if isinstance(item, ExportDestination)
            else FileSystemDestination.model_validate(item)
            for item in destinations
        ]

    chunk_size: int = Field(
        default=1_000,
        ge=1,
        validation_alias="ASSQUACK_CHUNK_SIZE",
    )

    # Keep schema.replace available without a field shadowing Pydantic's legacy
    # schema() method. JSON schema generation uses model_json_schema().
    @property
    def schema(self) -> SchemaConfig:  # pyright: ignore[reportIncompatibleMethodOverride]
        return self.schema_settings

    @model_validator(mode="after")
    def resolve_local_paths(self) -> Self:
        if self.database_path is None:
            self.database_path = self.home / "assquack.duckdb"
        if self.ingestion.fragment_directory is None:
            self.ingestion.fragment_directory = (
                self.resolved_database_path.parent / "fragments"
            )
        return self

    @property
    def export_destinations(self) -> list[ExportDestination]:
        # Validation normalizes the public single-or-list input.
        assert isinstance(self.exports, list)
        return self.exports

    @property
    def resolved_database_path(self) -> Path:
        assert self.database_path is not None
        return self.database_path

    @property
    def resolved_temp_directory(self) -> Path:
        return self.duckdb.temp_directory or self.home / "tmp"

    @property
    def resolved_fragment_directory(self) -> Path:
        assert self.ingestion.fragment_directory is not None
        return self.ingestion.fragment_directory
