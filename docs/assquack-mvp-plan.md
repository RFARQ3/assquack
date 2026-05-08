# Assquack MVP Plan Archive

This file is retained as the original monolithic planning note. The active
architecture docs are split by topic and indexed in [README.md](README.md).
Use [roadmap.md](roadmap.md) for MVP sequencing.

Later headings such as "Open Decisions" and "Current Recommendation" are
historical context from the original planning pass. They are not authoritative
when they conflict with the active split documentation.

---

Date: 2026-05-08

## Intent

Assquack is a standalone DuckDB-native data asset library. The earlier
discussion used the working name QuackAsset; this repo name is now the product
name. The goal is to keep a simple Python shape of `@asset`, `.query()`,
`cache_first()`, argument-bound assets, and manifest metadata, while avoiding
the current pattern of persisting large raw JSON or intermediate file fragments
just so DuckDB can infer and query schema later.

Assquack must not depend on Prefect. This repo is currently planning-only plus a
submodule reference to MAD.Prefect at `.submodules/MAD.Prefect` for historical
context and migration research.

## Reference: MAD.Prefect Today

The current MAD.Prefect data asset API is a decorator that accepts a path, optional
`artifacts_dir`, snapshot flag, artifact file type, read options, and cache TTL.
It returns a `DataAsset` with `.with_arguments()`, `.with_options()`,
`.cache_first()`, and `.query()` methods.

This section describes the reference implementation only. It is not an
Assquack dependency target.

Source references:

- `.submodules/MAD.Prefect/mad_prefect/data_assets/asset_decorator.py`
- `.submodules/MAD.Prefect/mad_prefect/data_assets/data_asset.py`
- `.submodules/MAD.Prefect/mad_prefect/data_assets/data_asset_callable.py`

The runtime lifecycle is:

1. Resolve templated asset name/path/options from bound arguments.
2. Read the last materialized timestamp from an asset manifest or legacy JSON
   run metadata.
3. If cache is valid and the result artifact exists, return it.
4. Delete prior fragment artifacts unless snapshotting is enabled.
5. Register the custom DuckDB `mad://` filesystem.
6. Persist yielded batches as JSON, Parquet, or CSV fragments.
7. Query all fragments through DuckDB and persist one or more final result
   artifacts.
8. Persist run metadata and update the manifest.

The storage abstraction is `FsspecFileSystem`, configured through
`FILESYSTEM_URL` or a Prefect filesystem block. The custom `mad://` filesystem
maps DuckDB file access back onto that fsspec base path.

Important current traits:

- Output compatibility is broad: Python objects, async yields, DataFrames,
  Arrow batches/tables, DuckDB relations, `DataArtifact`, nested `DataAsset`,
  and `httpx.Response` are handled.
- Querying is file-backed: JSON, Parquet, and CSV paths are passed into
  `read_json`, `read_parquet`, or `read_csv`.
- Metadata is filesystem-backed JSON under `_asset_metadata/...`.
- Snapshotting controls filesystem fragment retention, not table versions.
- Hydra config, deployment fragments, ABFSS-native DuckDB setup, Postgres,
  and MSSQL integration are not implemented in this submodule.

## Current Pain Points

- Large raw JSON dumps can be orders of magnitude bigger than the final Parquet
  asset. The observed 33 GB JSON to 1.43 GB Parquet case is a symptom of using
  raw files as schema inference evidence and replay input.
- Materialization is file-fragment first, table second. DuckDB is mostly the
  query/merge engine, not the storage engine.
- Current cache state is "result artifact exists and manifest says recent",
  not transactional table state.
- Incremental materialization is not first-class. Full snapshot, append, merge,
  and breadcrumb/delta semantics are not explicit asset modes.
- Writing fragments through fsspec plus PyArrow schema evolution creates a lot
  of logic that a DuckDB table could own directly.

## DuckDB Constraints And Opportunities

As of DuckDB 1.5 documentation:

- DuckDB's Azure extension supports reading and writing Azure Blob and ADLSv2
  paths, including `abfss://...`.
- DuckDB has a `JSON` logical type, but it is physically stored as `VARCHAR`;
  this is not a Postgres-style binary JSONB replacement.
- DuckDB 1.5 adds `VARIANT`, a typed binary semi-structured data type where
  each row carries its own type metadata. This is the JSONB-like storage option
  for dynamic structures and should be the default durable representation for
  unknown or volatile nested payloads.
- DuckDB can infer JSON schemas deeply, convert JSON to nested `LIST` and
  `STRUCT` types, and use `MAP` or `UNION` where data shape is sparse or
  genuinely variant.
- Native `.duckdb` database files are not a safe shared multi-process write
  target. For shared read-write use, DuckDB documentation points toward
  DuckLake with a central catalog such as PostgreSQL.
- The DuckDB Postgres extension lets DuckDB read and write PostgreSQL tables.
  It should be treated as source/sink integration, not as "DuckDB stored inside
  Postgres."

Primary references:

- https://duckdb.org/docs/current/core_extensions/azure
- https://duckdb.org/docs/current/sql/data_types/variant
- https://duckdb.org/docs/current/data/json/json_type
- https://duckdb.org/docs/current/data/json/loading_json
- https://duckdb.org/docs/current/data/json/json_functions
- https://duckdb.org/docs/current/sql/data_types/overview
- https://duckdb.org/docs/current/sql/data_types/union
- https://duckdb.org/docs/current/connect/concurrency
- https://duckdb.org/docs/current/core_extensions/postgres

## MVP Position

Use embedded DuckDB, in-process, with a `.duckdb` file at a local writable path
as the first write target.

Rationale:

- It is closest to DuckDB's design and avoids treating object storage as a
  mutable random-access database filesystem.
- It fits local development and any Python runtime without a database server.
- It removes the 33 GB raw JSON file as a required durable artifact.
- It can still export compatible Parquet snapshots to ADLS for lake access and
  legacy consumers.
- It keeps server-mode options such as MotherDuck, pg_duckdb, and DuckLake out
  of the MVP until concurrency and governance requirements are measured.

The MVP must enforce one writer per DuckDB database file. Use an Assquack lock
keyed by the resolved database path; external orchestrators can add their own
concurrency controls without becoming core dependencies.
Multiple readers are fine when the database is opened read-only or after
materialization completes.

## Storage Model

Default database URI:

```text
${ASSQUACK_HOME:-/data/assquack}/{environment}/assquack.duckdb
```

Database placement is configuration-level, not a first-class asset decorator
argument. Projects can still configure multiple database files later, but the
asset author should not need to think about database placement for the common
case.

Do not use ABFSS as the primary location for a mutable `.duckdb` file in the
MVP. Use ADLS for:

- exported current-state Parquet,
- exported run snapshots,
- recovery backups,
- source data reads,
- compatibility with existing datalake conventions.

Future shared-write option:

- DuckLake with PostgreSQL catalog and ADLS-backed data files.

## Asset Table Model

Each asset gets:

- a stable `asset_id` derived from name, table, export target, relevant config,
  and bound arguments;
- a sanitized schema/table name;
- a current table;
- staging tables per run;
- metadata rows in Assquack system tables.

Recommended system tables:

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
  uri VARCHAR NOT NULL,
  format VARCHAR NOT NULL,
  row_count BIGINT,
  created_at TIMESTAMPTZ NOT NULL
);
```

User asset tables should include reserved metadata columns:

```text
_qa_run_id
_qa_loaded_at
_qa_source_uri
_qa_source_hash
_qa_payload
_qa_raw_json
```

`_qa_payload` is optional per asset and should be `VARIANT` by default for
raw/bronze dynamic payloads. `_qa_raw_json` remains useful as an explicit audit
or lossless text-retention choice, but should not be the normal durable storage
for dynamic structures.

## Materialization Modes

MVP:

- `replace`: build a staging table for the run, then atomically replace the
  current table inside a transaction.

Near follow-up:

- `append`: append rows with `_qa_run_id` and `_qa_loaded_at`, optionally expose
  a current view.
- `merge`: require key columns and use DuckDB `MERGE INTO` to upsert current
  rows.
- `snapshot`: retain versioned historical rows or per-run tables and expose
  current/latest views.

The immediate MVP should implement `replace` cleanly before adding deltas. A
half-implemented incremental model would be harder to unwind than explicit full
refreshes with export snapshots.

## Semi-Structured Strategy

DuckDB `VARIANT` should be the default Assquack representation for dynamic
semi-structured payloads. DuckDB `JSON` is still useful for ingestion,
validation, structure discovery, and transformation, but it should not be our
JSONB-like storage primitive because the documented `JSON` logical type is
stored as text.

Recommended flow:

1. Stream API or source batches into a DuckDB staging table.
2. For unknown or volatile nested payloads, write a `_qa_payload VARIANT`
   column plus extracted typed columns.
3. Use `json_group_structure`, `json_structure`, and `json_transform` to record
   and apply schema evidence when the source arrives as JSON.
4. Store the observed structure in `assquack.schemas`.
5. Promote stable fields into typed columns over time.

For deeply nested or sparse objects:

- Prefer `VARIANT` for raw/bronze payload retention when row-level shapes vary.
- Prefer `STRUCT`/`LIST` when shape is stable.
- Prefer `MAP` when keys are numerous or sparse.
- Use `UNION` only for genuine variant values with a bounded set of types.
- Use Parquet `VARIANT` export with shredding where it improves downstream
  read performance and predicate pushdown.

## Yielded Batch Storage And Continuous Inference

Assquack should treat API output as observed data, not as a contract. Every
materialization should infer from the yielded rows it actually receives, and it
should continue updating schema evidence across runs.

Each run should create two staging surfaces:

1. **Raw evidence staging**: append every yielded row with fixed metadata and a
   `_qa_payload VARIANT` value.
2. **Shaped staging**: project known/promoted fields from `_qa_payload` into
   typed columns for fast SQL, while keeping `_qa_payload` available for fields
   that are new, sparse, or inconsistent.

Recommended raw staging shape:

```sql
CREATE TABLE assquack_stage.raw_<asset_id>_<run_id> (
  _qa_run_id VARCHAR NOT NULL,
  _qa_batch_id INTEGER NOT NULL,
  _qa_row_number BIGINT NOT NULL,
  _qa_loaded_at TIMESTAMPTZ NOT NULL,
  _qa_source_uri VARCHAR,
  _qa_source_hash VARCHAR,
  _qa_payload VARIANT NOT NULL
);
```

The materializer should load yielded batches in chunks:

1. Normalize the yield into rows. Accepted inputs are Python mappings/lists,
   async iterables, pandas DataFrames, Arrow tables/batches, DuckDB relations,
   and HTTP responses.
2. Insert rows into raw staging. For mapping/list rows, serialize only the
   current chunk when needed, cast into `VARIANT`, and discard the transient
   text after insertion. Do not persist a full raw JSON dump by default.
3. Run schema observation over the chunk and append path/type/count evidence to
   `assquack.schema_observations`.
4. Resolve the effective schema for this asset from all previous observations
   plus the current run's new observations.
5. Project the chunk into shaped staging using generated extraction and
   `try_cast` expressions. Missing fields become `NULL`.
6. Add newly discovered shaped columns during the run. Do not drop columns just
   because an API omitted them in the current batch.

Deep inference should use actual observed paths and values:

- For JSON-origin rows, use DuckDB JSON tooling on the transient chunk:
  `json_group_structure` for compact merged shape and `json_tree` for path-level
  evidence.
- When using `read_json` for a source file or transient chunk, prefer deep
  detection settings such as `maximum_depth = -1`, `sample_size = -1`, and
  `union_by_name = true` for inference jobs where full scanning is acceptable.
- For stored semi-structured values, use `VARIANT` as the durable payload and
  inspect with `variant_typeof`, dot access, and `variant_extract` for projected
  paths.

Type resolution policy should be conservative:

- `NULL` never defines a column type by itself.
- Numeric types widen rather than conflict.
- Date/time detection should record both the parsed type and failed parse count.
- A field that alternates between scalar/object/list should remain `VARIANT`
  until an asset author overrides it.
- Sparse objects with many keys should become `MAP` or stay `VARIANT`; stable
  objects can become `STRUCT`.
- Arrays with stable element shape can become `LIST(...)`; arrays with
  inconsistent element shape should remain `VARIANT`.
- Existing promoted columns are never removed automatically. Absence updates
  `last_seen_at`, not the schema.

This gives Assquack the same robustness goal as the current file-backed assets,
but moves the durable evidence into DuckDB tables instead of raw JSON files.

## Public Python Surface

Target package name:

```text
assquack
```

Minimum interfaces:

Developer-facing examples live in
[developer-examples.md](developer-examples.md).

The public decorator should preserve a simple path-first convention: one
positional path argument should be enough for most assets. That positional path
is an export alias and resolves to a `mad://...` export target by convention.

`name`, `table`, and `mode` are optional overrides. The default mode is
`replace`. There is no first-class database placement decorator argument.

Core classes:

- `AssquackAsset`: decorated asset definition.
- `AssquackAssetCallable`: execution lifecycle.
- `AssquackStore`: DuckDB connection, extension setup, transactions, table naming.
- `AssquackRun`: run metadata.
- `AssquackManifest`: latest known asset state.
- `AssquackResult`: returned table reference with `.query()`, `.to_df()`,
  `.export_parquet()`, and `.exists()`.
- `AssquackConfig`: Pydantic config with optional Hydra loader.

Compatibility inputs should be broad but framework-agnostic:

- list/dict batches,
- async iterables,
- pandas DataFrame,
- Arrow Table/RecordBatch,
- DuckDB relation,
- `httpx.Response`.

## Config Shape

Use Pydantic as the source of truth and provide Hydra config groups as an
adapter. That keeps this library usable outside Hydra while still fitting the
team's preferred config composition.

Suggested config groups:

```text
conf/assquack/default.yaml
conf/assquack/storage/local.yaml
conf/assquack/storage/adls_export.yaml
conf/assquack/duckdb/local.yaml
```

Example:

```yaml
assquack:
  home: ${oc.env:ASSQUACK_HOME,/data/assquack}
  environment: ${oc.env:ENVIRONMENT,dev}
  database_path: ${assquack.home}/${assquack.environment}/assquack.duckdb
  duckdb:
    threads: 4
    memory_limit: 8GB
    temp_directory: /data/assquack/tmp
    extensions:
      - json
      - parquet
      - azure
      - postgres
  exports:
    enabled: true
    base_uri: abfss://lake/exports/assquack
```

Runtime environment responsibility:

- provide a writable filesystem path for `ASSQUACK_HOME`;
- expose `ASSQUACK_HOME`;
- optionally expose `DUCKDB_TEMP_DIRECTORY`;
- provide external concurrency controls only if the embedding runtime needs
  them;
- make ADLS credentials available as environment variables or DuckDB secrets.

## Optional Legacy Adapters

Do not fork MAD.Prefect's data asset code into this repo wholesale, and do not
make Prefect a core dependency.

Instead:

1. Build Assquack independently with framework-agnostic primitives.
2. Export Parquet to ADLS so existing file-backed consumers can continue.
3. If needed later, create a separate optional adapter package for MAD.Prefect
   projects. That adapter must depend on Assquack, not the other way around.

## MVP Build Phases

### Phase 0: Repo Bootstrap

- Add Python package skeleton.
- Add `pyproject.toml`, tests, lint/type tooling.
- Pin DuckDB to a 1.5+ baseline so `VARIANT` is available.

### Phase 1: Local DuckDB Core

- Implement `AssquackConfig`.
- Implement `AssquackStore` with connect, transaction, schema bootstrap, and
  extension setup.
- Implement metadata tables and manifest read/write.
- Add tests using temporary `.duckdb` files.

### Phase 2: Replace Materialization

- Implement `@asset`.
- Support async/sync yielded batches.
- Load supported Python/Arrow/DuckDB inputs into raw evidence staging and shaped
  staging.
- Implement chunk-level schema observation and conservative type resolution.
- Replace current table transactionally.
- Return `AssquackResult`.

### Phase 3: Query And Cache

- Implement `.query(sql=None, params=None)`.
- Implement `cache_first()` and TTL logic from metadata tables.
- Implement `.exists()` based on table and latest successful run.

### Phase 4: Export Compatibility

- Export current table to Parquet.
- Support `abfss://` export using DuckDB Azure extension where available.
- Keep a fsspec fallback only if DuckDB Azure extension cannot satisfy the
  deployment target.

### Phase 5: Semi-Structured Evidence

- Add optional `_qa_payload VARIANT`.
- Store `json_group_structure` output per run.
- Add helpers to promote fields from `VARIANT` or JSON into typed columns.
- Add Parquet export tests for `VARIANT`, including shredding where supported.

### Phase 6: Incremental Modes

- Add `append`.
- Add `merge` with required key columns.
- Add snapshot/history views.

### Phase 7: Shared Storage Exploration

- Prototype DuckLake with PostgreSQL catalog.
- Evaluate MotherDuck only if the embedded local database model is insufficient
  for the desired sharing/concurrency model.
- Evaluate external-source extensions such as Postgres and MSSQL as query
  inputs, not core Assquack storage.

## Early Acceptance Criteria

The first useful MVP should prove:

- an asset can materialize to a local `.duckdb` file on disk;
- the same asset can be queried without reading Parquet/JSON fragments;
- cache-first skips rematerialization;
- run metadata survives process restart;
- Parquet export can be written for lake compatibility;
- a nested dynamic payload can be stored as `VARIANT` without writing a raw JSON
  dump first;
- tests cover local filesystem behavior, batch-level schema drift, cross-run
  schema drift, and at least one nested JSON schema evolution case.

## Open Decisions

- Default database placement and whether projects need multiple configured
  database files.
- Minimum DuckDB version: `VARIANT` makes DuckDB 1.5+ the likely Assquack
  baseline.
- Whether exported Parquet is mandatory for every asset or opt-in.
- Whether `_qa_payload VARIANT` should default on for raw/bronze assets only,
  and whether `_qa_raw_json` should be opt-in audit storage.
- Whether Hydra config lives in this package or in consuming flow repos.
- Whether table names should be user-facing stable names or opaque `asset_id`
  names plus views.

## Current Recommendation

Start with embedded DuckDB on a local writable path, one writer per database
file, `replace` materialization, metadata tables inside DuckDB, and optional
Parquet export to ADLS. Keep DuckLake/Postgres catalog as the planned answer
for shared multi-process writes, not the first implementation target.
