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
    azure_auth: Literal["auto", "credential_chain", "sas"] = Field(
        default="auto",
        validation_alias="ASSQUACK_EXPORTS__AZURE_AUTH",
    )
    azure_chain: (
        Literal["cli", "managed_identity", "workload_identity", "env", "default"] | None
    ) = Field(
        default=None,
        validation_alias="ASSQUACK_EXPORTS__AZURE_CHAIN",
    )
    azure_account_name: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9]{3,24}$",
        validation_alias="ASSQUACK_EXPORTS__AZURE_ACCOUNT_NAME",
    )

    @model_validator(mode="after")
    def validate_azure_auth(self) -> Self:
        if self.azure_auth == "sas" and self.azure_chain is not None:
            raise ValueError("azure_chain cannot be combined with azure_auth='sas'.")
        return self


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
    exports: ExportsConfig = Field(default_factory=ExportsConfig)
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

    @property
    def resolved_fragment_directory(self) -> Path:
        assert self.ingestion.fragment_directory is not None
        return self.ingestion.fragment_directory
