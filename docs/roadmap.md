# Assquack Roadmap

Status: planning split as of 2026-05-08.

This roadmap extracts implementation sequencing from the MVP plan. It tracks
what to build, what should be true early, and which decisions remain open.
Architecture detail belongs in the focused design docs.

## Baseline

- DuckDB 1.5+ is the baseline so `VARIANT` is available for durable
  semi-structured payloads.
- The MVP starts with embedded, in-process DuckDB and a local writable
  `.duckdb` file.
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
- Load supported Python, Arrow, pandas, and DuckDB inputs into staging.
- Always retain raw staging evidence in `_qa_payload VARIANT` during the run so
  deep schema inference does not require durable raw JSON dumps.
- Record chunk-level schema observations and resolve types conservatively.
- Transactionally replace the current asset table from staging.
- Prune staging to one latest successful raw table and three failed run staging
  sets per asset.
- Return a result object that can be queried by downstream code.

### Phase 3: Query And Cache

- Implement `.query(sql=None, params=None)` against the current table.
- Implement `cache_first()` and TTL behavior from Assquack metadata.
- Implement `.exists()` based on table presence and the latest successful run.

### Phase 4: Export Compatibility

- Export the current table to Parquet.
- Support `abfss://` exports through the DuckDB Azure extension where it works
  in the target deployment.
- Keep a fsspec export fallback only if DuckDB-native Azure support is not
  sufficient for the deployment target.

### Phase 5: Semi-Structured Evidence Policy

- Add configurable retention policy for when `_qa_payload VARIANT` is kept in
  final shaped/current tables versus used only in raw staging. Do not treat
  `_qa_payload` as universally present or universally `NOT NULL` in current
  tables.
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
- No full raw JSON dump is required as the durable replay artifact for ordinary
  API-shaped data.
- Unknown or changing nested payloads can be retained as `VARIANT` while stable
  fields are promoted into typed columns.
- The project can export current-state Parquet for legacy lake consumers.
- Tests cover temporary local DuckDB files, metadata bootstrap, replace
  materialization, cache checks, and basic export behavior.

## Open Decisions

- Exact public naming for result/query methods beyond the minimum compatibility
  surface.
- How much Hydra configuration should ship in the core package versus examples
  or adapters.
- The first supported ADLS credential pattern for DuckDB Azure exports.
- The default policy for when `_qa_payload` is retained, omitted, or made
  mandatory in shaped/current tables. Raw staging keeps `_qa_payload VARIANT`
  during MVP materialization and bounded diagnostic retention.
- How schema promotion should be declared by asset authors without turning the
  decorator into a storage configuration surface.
- Whether legacy MAD.Prefect compatibility needs a separate adapter package, and
  when to build it.

## Future Incremental Modes

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
