# Local DuckDB Core

Status: **Planned**
Last updated: 2026-05-08
Epic: 01 MVP
Phase: 01
Related docs: [Roadmap](../../roadmap.md), [Storage Model](../../storage-model.md), [Configuration](../../configuration.md)

## Intent

Create the embedded DuckDB foundation that all MVP asset behavior depends on:
configuration, connection setup, metadata tables, and one-writer coordination.

## Scope

Included:

- Pydantic `AssquackConfig` as the source of truth for runtime configuration.
- DuckDB connection setup with deterministic settings and extensions.
- Assquack metadata schema bootstrap.
- Application-level writer lock keyed by resolved database path.
- Store-level tests using temporary `.duckdb` files.

Excluded:

- Asset decorator lifecycle.
- Schema inference policy beyond table creation.
- Export writes.

## Architecture Notes

The core shape should separate configuration, connection lifecycle, metadata
bootstrap, and writer locking. A small repository or unit-of-work boundary is
appropriate if it keeps DuckDB transaction handling obvious to future
maintainers.

## Metadata Table Contracts

Bootstrap should create the `assquack` schema and the MVP system tables:

```sql
CREATE SCHEMA IF NOT EXISTS assquack;

CREATE TABLE assquack.assets (
  asset_id VARCHAR PRIMARY KEY,
  asset_name VARCHAR NOT NULL,
  asset_signature VARCHAR,
  schema_name VARCHAR NOT NULL,
  table_name VARCHAR NOT NULL,
  materialization_mode VARCHAR NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE assquack.runs (
  run_id VARCHAR PRIMARY KEY,
  asset_id VARCHAR NOT NULL,
  status VARCHAR NOT NULL,
  runtime TIMESTAMPTZ NOT NULL,
  materialized_at TIMESTAMPTZ,
  duration_ms BIGINT,
  row_count BIGINT,
  error VARCHAR
);

CREATE TABLE assquack.schemas (
  asset_id VARCHAR NOT NULL,
  run_id VARCHAR NOT NULL,
  schema_json JSON,
  json_structure JSON,
  created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE assquack.schema_observations (
  asset_id VARCHAR NOT NULL,
  run_id VARCHAR NOT NULL,
  path VARCHAR NOT NULL,
  observed_type VARCHAR NOT NULL,
  present_count BIGINT NOT NULL,
  null_count BIGINT NOT NULL,
  total_count BIGINT NOT NULL,
  first_seen_at TIMESTAMPTZ NOT NULL,
  last_seen_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE assquack.exports (
  export_id VARCHAR PRIMARY KEY,
  run_id VARCHAR NOT NULL,
  asset_id VARCHAR NOT NULL,
  alias VARCHAR,
  uri VARCHAR NOT NULL,
  resolved_uri VARCHAR NOT NULL,
  format VARCHAR NOT NULL,
  role VARCHAR NOT NULL,
  row_count BIGINT,
  byte_count BIGINT,
  content_hash VARCHAR,
  options_json JSON,
  created_at TIMESTAMPTZ NOT NULL
);
```

## Implementation Checklist

- [ ] Implement typed configuration models.
- [ ] Resolve database path, temp directory, and DuckDB settings.
- [ ] Open DuckDB connections and apply configured settings.
- [ ] Bootstrap `assquack` system tables.
- [ ] Add writer-lock abstraction for one writer per database path.
- [ ] Add tests for config defaults, validation errors, and metadata bootstrap.
- [ ] Update this status header before handoff.

## Validation

- `poetry run pytest`
- `poetry run pyright`
- `poetry run ruff check .`
- `poetry run ruff format --check .`
- `git diff --check`

## Notes

Keep the storage boundary explicit. The mutable `.duckdb` file is local
configuration state; export paths are not part of database placement.
