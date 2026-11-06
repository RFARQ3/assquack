from __future__ import annotations

from datetime import timedelta
from enum import Enum
from typing import Literal, TypedDict

from assquack._storage.sql import sanitize_identifier

AssetMode = Literal["replace"]

ExportFormat = Literal["parquet", "json", "csv"]


class ExportSpec(TypedDict):
    uri: str
    format: ExportFormat


class Unset(Enum):
    VALUE = "unset"


def validate_options(
    table: str, mode: AssetMode, cache_expiration: timedelta | None
) -> None:
    validate_table(table)
    if cache_expiration is not None and cache_expiration < timedelta(0):
        raise ValueError("Cache expiration cannot be negative.")
    if mode != "replace":
        raise ValueError("The Assquack prototype only supports mode='replace'.")


def validate_table(table: object) -> None:
    if not isinstance(table, str):
        raise TypeError("Asset table must be a non-empty string.")
    if not table.strip():
        raise ValueError("Asset table must be a non-empty string.")
    if any(not sanitize_identifier(part, fallback="") for part in table.split(".")):
        raise ValueError("Asset table must contain a non-empty schema/table name.")
