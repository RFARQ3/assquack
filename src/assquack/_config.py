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
    environment: str = Field(
        default="dev",
        validation_alias=AliasChoices("ASSQUACK_ENVIRONMENT", "ENVIRONMENT"),
    )
    database_path: Path | None = Field(
        default=None,
        validation_alias="ASSQUACK_DATABASE_PATH",
    )
    duckdb: DuckDBConfig = Field(default_factory=DuckDBConfig)
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
    schema_settings: SchemaConfig = Field(default_factory=SchemaConfig, alias="schema")
    exports: (
        SerializeAsAny[FileSystemDestination | ExportDestination]
        | list[SerializeAsAny[FileSystemDestination | ExportDestination]]
    ) = Field(
        default_factory=list[ExportDestination], validation_alias="ASSQUACK_EXPORTS"
    )

    @field_validator("exports", mode="before")
    @classmethod
    def normalize_exports(cls, value: object) -> list[ExportDestination]:
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
            self.database_path = self.home / self.environment / "assquack.duckdb"
        if self.duckdb.temp_directory is None:
            self.duckdb.temp_directory = self.home / "tmp"
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
        assert self.duckdb.temp_directory is not None
        return self.duckdb.temp_directory

    @property
    def resolved_fragment_directory(self) -> Path:
        assert self.ingestion.fragment_directory is not None
        return self.ingestion.fragment_directory
