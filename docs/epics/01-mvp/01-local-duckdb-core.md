# Local DuckDB Core

Status: **In Progress**
Last updated: 2026-11-02
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

Bootstrap creates the `assquack_meta` schema and the MVP system tables:

```sql
CREATE SCHEMA IF NOT EXISTS assquack_meta;

CREATE TABLE assquack_meta.assets (
  asset_id VARCHAR PRIMARY KEY,
  asset_name VARCHAR NOT NULL,
  asset_signature VARCHAR,
  schema_name VARCHAR NOT NULL,
  table_name VARCHAR NOT NULL,
  materialization_mode VARCHAR NOT NULL,
  first_materialized_at TIMESTAMPTZ,
  last_materialized_at TIMESTAMPTZ
);

CREATE TABLE assquack_meta.runs (
  run_id VARCHAR PRIMARY KEY,
  asset_id VARCHAR NOT NULL,
  status VARCHAR NOT NULL,
  runtime TIMESTAMPTZ NOT NULL,
  materialized_at TIMESTAMPTZ,
  duration_ms BIGINT,
  row_count BIGINT,
  error VARCHAR,
  fragments_owned BOOLEAN DEFAULT true
);

CREATE TABLE assquack_meta.exports (
  export_id VARCHAR PRIMARY KEY,
  run_id VARCHAR NOT NULL,
  asset_id VARCHAR NOT NULL,
  alias VARCHAR,
  uri VARCHAR NOT NULL,
  resolved_uri VARCHAR NOT NULL,
    destination_fingerprint VARCHAR NOT NULL,
  format VARCHAR NOT NULL,
  role VARCHAR NOT NULL,
  row_count BIGINT,
  byte_count BIGINT,
  content_hash VARCHAR,
  options_json JSON,
  created_at TIMESTAMPTZ NOT NULL
);
```

Schema-history and observation DDL was removed on 26 October 2026 as part of
[EXT-05](06-extensibility-defects.md#ext-05-schema-strategy-selection-and-context).
The published table owns its schema. No migration or deletion of existing
records is performed.

## Implementation Checklist

Publication timestamp correction is complete. Registration leaves both
asset timestamps null. Successful publication updates them in the table/run
transaction; cache hits and failed attempts do not. Bootstrap replaces the old
registration timestamps using only successful run evidence, never their values.
Validation covers first/subsequent success, capture and transaction failures,
cache hits, export failures, historical migration, and repeated bootstrap with
synthetic DBs. Independent review passed with no remaining actionable defects.

- [ ] Implement typed configuration models.
- [ ] Resolve database path, temp directory, and DuckDB settings.
- [ ] Open DuckDB connections and apply configured settings.
- [ ] Bootstrap `assquack` system tables.
- [x] Add writer-lock abstraction for one writer per database path.
- [ ] Add tests for config defaults, validation errors, and metadata bootstrap.
- [ ] Update this status header before handoff.

## Validation

- `poetry run pytest`
- `poetry run pyright`
- `poetry run ruff check .`
- `poetry run ruff format --check .`
- `git diff --check`

## Notes

### Fragment ownership metadata

Retention adds `runs.fragments_owned` through an idempotent additive bootstrap
migration. Legacy catalog records default to owned; new attempts explicitly
start unowned and claim ownership only after exclusive fragment-directory
creation succeeds. This keeps a failed claim from turning an orphan directory
into deletion-eligible evidence. No metadata records are deleted or expired.
See the [replace phase](02-replace-materialization.md#staging-retention-contract)
for the retention and interruption policy. This phase remains **In Progress**;
the existing broader core checklist has not been revalidated by this change.

Keep the storage boundary explicit. The mutable `.duckdb` file is local
configuration state; export paths are not part of database placement.

## Destination fingerprint metadata — 27 October 2026

The exports DDL includes `destination_fingerprint` alongside `resolved_uri`.
Successful output records and cache requirements use both; asset identity is
unchanged. This is a pre-release schema change without a migration shim. The
[export phase](04-export-compatibility.md) tracks validation and the unresolved
native credential-chain decision. The broader core phase remains In Progress.

## Home-based Paths — 29 October 2026

Implemented: removed the environment setting and its aliases.
Default the database to `<home>/assquack.duckdb`, fragments beside the resolved
database, and fallback local exports to `<home>/exports`. Preserve explicit
path overrides and `ASSQUACK_HOME`; no compatibility shim or migration.
Validation: 279 tests passed; 8 optional pandas/PyArrow tests skipped. Ruff lint,
changed-file formatting, Poetry metadata, documentation links, and diff checks
passed. Pyright reports 7 errors in unchanged API/query/template code. The full
format check flags existing line endings in 8 untouched files. The broader core
phase remains In Progress; this path simplification is complete.

## Publication timestamps — 2 November 2026

`assets.first_materialized_at` and `last_materialized_at` replace registration
dates. `save_asset` writes identity/configuration only. The existing replace
transaction updates the table, successful run, and asset dates together, using
the run's exact `materialized_at`. Append remains unsupported. Export failures
occur after commit and cannot erase publication dates.

The explicitly requested migration is limited to the old asset timestamp schema:
add nullable columns, drop registration columns, then backfill successful run
MIN/MAX in one transaction. DuckDB requires these ALTERs before the UPDATE;
altering again after data modification can fail at commit. Run metadata is not
pruned with fragments. Missing evidence stays null; manually removed history
limits dates to the surviving evidence. Current-schema bootstrap never recalculates
dates. No customer databases were accessed or migrated.

Validation: 369 tests passed on Python 3.11 / DuckDB 1.5.6, including six focused
timestamp cases. They cover rollback after metadata writes, interrupted migration
restoring old schema/data, and repeated bootstrap after historical run removal.
Ruff lint, changed-file formatting, documentation links, and `git diff --check`
passed. Terminology scan found only the existing historical Kubernetes note and
its references. Pyright reports the same seven existing API/query/template
diagnostics, none in this change. The broader core phase remains **In Progress**.
Independent review passed 45 targeted publication, rollback, migration, cache,
and retention tests and approved the implementation without outstanding findings.
