# Environment-backed Configuration

Status: Working MVP requirement observation

This document records prototype behaviour and does not change the contracts in
the canonical documentation.

## Observed Requirement

The canonical configuration model identifies process environment variables as
a configuration source below direct and adapter-composed values but above
Pydantic defaults. The initial prototype only read `ASSQUACK_HOME` and
`ENVIRONMENT`, leaving the rest of the model unavailable to deployments that
configure applications through environment variables.

Every runtime field should be configurable without passing an
`AssquackConfig` instance to each asset call.

## MVP Behaviour

`AssquackConfig`, `DuckDBConfig`, and `ExportsConfig` use Pydantic settings to
read process environment variables. Explicit Python values retain higher
precedence than environment values.

The prototype environment names are:

```text
ASSQUACK_HOME
ASSQUACK_ENVIRONMENT
ASSQUACK_DATABASE_PATH
ASSQUACK_CHUNK_SIZE
ASSQUACK_DUCKDB__THREADS
ASSQUACK_DUCKDB__MEMORY_LIMIT
ASSQUACK_DUCKDB__TEMP_DIRECTORY
ASSQUACK_DUCKDB__EXTENSIONS
ASSQUACK_EXPORTS__ENABLED
ASSQUACK_EXPORTS__BASE_URI
ASSQUACK_EXPORTS__DEFAULT_FORMAT
ASSQUACK_EXPORTS__SNAPSHOT_RUNS
ASSQUACK_EXPORTS__OPTIONS
```

The documented `ENVIRONMENT` and `DUCKDB_TEMP_DIRECTORY` names remain accepted.
The `ASSQUACK_` names take precedence when both forms are present.

Complex values remain strings at the operating-system boundary and use JSON
encoding for Pydantic conversion:

```text
ASSQUACK_DUCKDB__EXTENSIONS=["json","parquet"]
ASSQUACK_EXPORTS__OPTIONS={"compression":"zstd"}
```

## Dotenv Boundary

Assquack reads the process environment only. Its settings models do not select
or load a `.env` file. Applications may populate their own process environment
before constructing `AssquackConfig`.

## Current Prototype Location

```text
src/assquack/_config.py
tests/test_config.py
```

## Compatibility With Canonical Docs

This implements the existing canonical precedence and Pydantic-settings
direction. The systematic nested variable names are prototype detail because
the canonical docs do not yet prescribe names for every field.
