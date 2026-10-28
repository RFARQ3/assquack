# Assquack Roadmap

Status: first-release scope narrowed by Rowan on 2026-10-21.
Export formats clarified by Rowan on 2026-10-23: Parquet, CSV and JSON are required.

This roadmap extracts implementation sequencing from the MVP plan. It tracks
what to build, what should be true early, and which decisions remain open.
Architecture detail belongs in the focused design docs. The
[finalized scope journal](journal/2026-09-08-mvp-scope.md) records Rowan's decisions
and owns the release boundary where older architecture or phase documents differ.
The implementation sequence and acceptance checks are recorded there.

## Baseline

- Replacing useful MAD.Prefect data-asset workflows guides MVP scope decisions.
  Use its behavior as a migration reference without adding a core dependency or
  overriding explicit scope exclusions.
- DuckDB 1.5+ is the baseline so `VARIANT` is available for durable
  semi-structured payloads.
- The MVP starts with embedded, in-process DuckDB and a local writable
  `.duckdb` file.
- API-shaped inputs are captured in bounded local NDJSON fragments beside the
  database before DuckDB infers and builds the typed table.
- The MVP enforces one writer per resolved database path. Shared write storage
  is a later exploration track.
- ADLS/object storage is for exports, snapshots, backups, and source reads, not
  the first mutable database target.

## MVP Phases

### Phase 0: Repo Bootstrap

- Add the Python package skeleton.
- Add `pyproject.toml`, tests, linting, and type tooling.
- Pin dependencies around the DuckDB 1.5+ baseline.

### Phase 1: Local DuckDB Core

- Implement configuration loading with Pydantic as the source of truth.
- Implement the DuckDB store wrapper for connection setup, transactions,
  extension setup, and schema bootstrap.
- Add Assquack metadata tables and manifest read/write behavior.
- Cover core store behavior with tests that use temporary `.duckdb` files.

### Phase 2: Replace Materialization

- Implement the `@asset` decorator and asset callable lifecycle.
- Support sync and async yielded batches.
- Normalize supported Python, Arrow, pandas, DuckDB, and Assquack result/asset inputs
  for fragment capture; preserve argument binding and path templates.
- Write local NDJSON fragments using a configurable megabyte target; never split
  records, and allow the last record to exceed the target.
- Infer across every completed fragment with DuckDB's native JSON reader.
- Implement current-run schema inference behind `schema.replace`; defer schema
  continuation and detailed observations.
- Transactionally replace the current asset table from staging. Failed runs keep
  the last published table; retries start fresh without reusing failed fragments.
- Prune local fragments to the latest successful run and three failed runs per
  asset.
- Return a result object that can be queried by downstream code.

### Phase 3: Query And Cache

- Implement `.query(sql=None, params=None)` against the current table.
- Implement `cache_first()` and TTL behavior from Assquack metadata.
- Implement `.exists()` based on table presence and the latest successful run.

### Phase 4: Export Compatibility

- Export the current table to local filesystem and Azure destinations. Both are
  MVP requirements; preserve the last successful output on failed materialization.
- Support Parquet, CSV and JSON as the starting export formats, with Parquet as
  the default and format writers separate from destination selection.
- Support pipe-separated formats such as `orders.parquet|csv|json`, expanding
  one declaration into multiple outputs from the same committed table.
- Support `abfss://` exports through the DuckDB Azure extension where it works
  in the target deployment.
- Use native DuckDB credential-chain or SAS authentication with explicit
  selection and an environment SAS provider; no fsspec fallback in MVP.

## Deferred: Semi-Structured Evidence Policy

This track is outside the first release. It must not block the replace MVP.

- Add configurable retention policy for when `_qa_payload VARIANT` is kept in
  final shaped/current tables. Do not treat `_qa_payload` as universally present
  or universally `NOT NULL` in current tables.
- Add helpers to promote stable fields from semi-structured payloads into typed
  columns.
- Add explicit audit options for `_qa_raw_json` where lossless source text is a
  business requirement.
- Test Parquet export behavior for `VARIANT`, including shredding when
  supported.

## Early Acceptance Criteria

- A developer can define a basic `@asset`, materialize it into a local DuckDB
  file, and query the result without Prefect.
- A repeated run in `replace` mode updates the current table transactionally and
  records a successful run in metadata.
- `cache_first()` returns the existing materialization when TTL rules allow it.
- Local NDJSON fragments provide bounded working and replay evidence without
  following the export destination into object storage.
- Unknown or changing nested payloads can be retained as `VARIANT` while stable
  fields are promoted into typed columns.
- The project can export current-state Parquet, CSV and JSON locally and to Azure for legacy
  lake consumers. Verify the agreed MAD.Prefect compatibility baseline.
- Tests cover temporary local DuckDB files, metadata bootstrap, replace
  materialization, cache checks, and basic export behavior.

## Implementation Details And Later Design

These details do not reopen the finalized release boundary. Historical evidence,
promotion and optional adapters below are later design work, not MVP gates.

- Exact public naming for result/query methods beyond the minimum compatibility
  surface.
- How much Hydra configuration should ship in the core package versus examples
  or adapters.
- Additional Azure authentication methods and credential providers beyond the
  agreed MVP [credential chain and environment SAS](configuration.md#adls-credentials).
- The default policy for when `_qa_payload` is retained or omitted from
  shaped/current tables. It is no longer mandatory for ingestion staging.
- How schema promotion should be declared by asset authors without turning the
  decorator into a storage configuration surface.
- Whether legacy MAD.Prefect compatibility needs a separate adapter package, and
  when to build it.

## Future Incremental Modes

The [8 September scope journal](journal/2026-09-08-mvp-scope.md) records Rowan's
21 October 2026 decision: merge-related work, including a minimal primary-key
merge, delta parameters and merge-specific schema quarantine, is outside MVP.
The remaining huddle suggestions have finalized dispositions in that journal.

These are intentionally after the `replace` MVP. A clean full-refresh path is
the release gate before incremental behavior.

- `append`: insert new rows with Assquack run metadata and optionally expose a
  current view.
- `merge`: require key columns and use DuckDB upsert behavior to maintain the
  current table.
- `snapshot`: retain historical versions or per-run tables while exposing
  current/latest views.
- Breadcrumb or delta-driven materialization: evaluate after the simpler
  incremental modes prove out.

## Shared Storage Exploration

The first shared-write exploration target is DuckLake with a PostgreSQL catalog
and object-storage-backed data files. That path should answer concurrency,
governance, and operational questions without changing the MVP's embedded
developer experience.

Evaluate MotherDuck only if the embedded local database model is insufficient
for the desired sharing or concurrency model. Treat external database
extensions, including PostgreSQL and MSSQL, as source/sink integrations unless a
future storage decision explicitly changes that status.

## Related Docs

- [Overview](overview.md): the product stance behind the roadmap.
- [Developer API](developer-api.md): the public surface built during the MVP.
- [Materialization Lifecycle](materialization-lifecycle.md): the `replace`
  lifecycle that gates later incremental modes.
- [Schema Inference](schema-inference.md): the semi-structured evidence track.
- [Storage Model](storage-model.md): local embedded storage and future shared
  storage options.
- [Exports](exports.md): compatibility export work in the MVP.
- [Configuration](configuration.md): configuration loading and runtime
  responsibilities.
