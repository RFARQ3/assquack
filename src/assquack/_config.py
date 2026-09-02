from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator


def _default_home() -> Path:
    return Path(os.environ.get("ASSQUACK_HOME", "/data/assquack"))


def _default_environment() -> str:
    return os.environ.get("ENVIRONMENT", "dev")


class DuckDBConfig(BaseModel):
    """Settings applied when Assquack opens its DuckDB database."""

    threads: int = Field(default=4, ge=1)
    memory_limit: str = "8GB"
    temp_directory: Path | None = None
    extensions: tuple[str, ...] = ()


class ExportsConfig(BaseModel):
    """Compatibility export settings; DuckDB tables remain authoritative."""

    enabled: bool = False
    base_uri: str | None = None
    default_format: Literal["parquet"] = "parquet"
    snapshot_runs: bool = False
    options: dict[str, object] = Field(default_factory=dict)


class AssquackConfig(BaseModel):
    """Authoritative runtime configuration accepted by the core package."""

    home: Path = Field(default_factory=_default_home)
    environment: str = Field(default_factory=_default_environment)
    database_path: Path | None = None
    duckdb: DuckDBConfig = Field(default_factory=DuckDBConfig)
    exports: ExportsConfig = Field(default_factory=ExportsConfig)
    chunk_size: int = Field(default=1_000, ge=1)

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
