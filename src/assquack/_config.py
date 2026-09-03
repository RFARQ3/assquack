from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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


class ExportsConfig(_ProcessSettings):
    """Compatibility export settings; DuckDB tables remain authoritative."""

    enabled: bool = Field(
        default=False,
        validation_alias="ASSQUACK_EXPORTS__ENABLED",
    )
    base_uri: str | None = Field(
        default=None,
        validation_alias="ASSQUACK_EXPORTS__BASE_URI",
    )
    default_format: Literal["parquet"] = Field(
        default="parquet",
        validation_alias="ASSQUACK_EXPORTS__DEFAULT_FORMAT",
    )
    snapshot_runs: bool = Field(
        default=False,
        validation_alias="ASSQUACK_EXPORTS__SNAPSHOT_RUNS",
    )
    options: dict[str, object] = Field(
        default_factory=dict,
        validation_alias="ASSQUACK_EXPORTS__OPTIONS",
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
    exports: ExportsConfig = Field(default_factory=ExportsConfig)
    chunk_size: int = Field(
        default=1_000,
        ge=1,
        validation_alias="ASSQUACK_CHUNK_SIZE",
    )

    @model_validator(mode="after")
    def resolve_local_paths(self) -> Self:
        if self.database_path is None:
            self.database_path = self.home / self.environment / "assquack.duckdb"
        if self.duckdb.temp_directory is None:
            self.duckdb.temp_directory = self.home / "tmp"
        if self.exports.base_uri is None:
            self.exports.base_uri = str(self.home / self.environment / "exports")
        return self

    @property
    def resolved_database_path(self) -> Path:
        assert self.database_path is not None
        return self.database_path

    @property
    def resolved_temp_directory(self) -> Path:
        assert self.duckdb.temp_directory is not None
        return self.duckdb.temp_directory
