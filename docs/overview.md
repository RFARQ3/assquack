# Assquack Overview

Assquack is a standalone DuckDB-native data asset library for Python. It gives
asset authors a small, framework-agnostic surface for defining materialized data
assets while making DuckDB tables, transactions, and metadata the durable source
of truth.

Assquack exists to keep the ergonomic parts of prior asset workflows, such as a
simple `@asset` decorator, queryable results, cache-first reads, argument-bound
assets, and manifest metadata, without carrying forward the dependency on
Prefect or the file-fragment-first storage model.

## What Assquack Is

Assquack is a Python library for turning functions into DuckDB-backed assets. A
decorated function yields or returns rows, batches, DataFrames, Arrow objects,
DuckDB relations, or other supported source shapes. Assquack normalizes that
data, materializes it into DuckDB staging and current tables, records run and
schema metadata, and exposes query/cache/export operations around the resulting
table state.

The library is intended to run inside ordinary Python processes. Orchestrators,
deployment systems, and configuration frameworks may call it, but they are not
part of the core contract.

## What Assquack Is Not

Assquack is not a Prefect library, a Prefect fork, or a wrapper around Prefect
blocks. The MAD.Prefect codebase is reference material for migration research
only.

Assquack is also not a filesystem-fragment asset runtime. It should not persist
large raw JSON dumps, CSV shards, or intermediate file fragments merely so
DuckDB can infer schema later. Files remain useful for source reads, Parquet
exports, compatibility snapshots, backups, and lake integration, but the MVP
stores asset state in DuckDB.

Assquack is not a shared database server in the MVP. It starts with embedded
DuckDB and a local writable `.duckdb` file, then evaluates shared-write options
only after the local model is proven.

## Design Philosophy

**Simple Python assets.** The common asset should be a decorated Python
function, not a deployment object. Assquack should support sync and async
producers, broad batch input types, queryable results, and cache checks without
requiring an orchestrator-specific runtime.

**Path-first convention.** The default authoring shape is path-first: a single
positional string acts as an export alias, while Assquack infers the asset name,
internal table, and default materialization mode. Overrides such as `name`,
`table`, and `mode` are escape hatches. Database placement belongs to Assquack
configuration, not per-asset decorators.

**DuckDB-native storage.** DuckDB is the storage and query engine, not just a
merge step over temporary files. Asset state lives in DuckDB tables, run history
and manifest data live in Assquack system tables, and materialization should use
DuckDB transactions where possible. Semi-structured source payloads should use
DuckDB-native types, especially `VARIANT` for volatile nested data, instead of
defaulting to durable raw JSON text.

**Export compatibility without storage leakage.** Parquet and ADLS exports are
important compatibility surfaces, but they are outputs of materialization, not
the primary state model.

## MVP Stance

The MVP uses embedded DuckDB, in process, with a configured local `.duckdb` file
as the first write target. It enforces one writer per database path, allows
readers after materialization completes, and implements `replace` materialization
before adding incremental modes.

Initial scope should focus on:

- core package and configuration primitives;
- DuckDB connection, locking, transactions, and metadata tables;
- `@asset` with path-first export aliases;
- raw evidence and shaped staging tables;
- conservative schema observation across runs;
- transactional replacement of the current table;
- `.query()`, `cache_first()`, and existence checks;
- Parquet export compatibility.

Append, merge, snapshot/history modes, DuckLake, MotherDuck, and optional
orchestrator adapters are follow-up work. Any Prefect adapter should depend on
Assquack, not the other way around.

## Related Docs

- [Developer API](developer-api.md): the minimal public contract implied by the
  design philosophy.
- [Materialization Lifecycle](materialization-lifecycle.md): how a decorated
  function becomes a DuckDB table.
- [Schema Inference](schema-inference.md): why raw `VARIANT` evidence is core to
  the asset model.
- [Storage Model](storage-model.md): where source-of-truth tables and metadata
  live.
- [Roadmap](roadmap.md): implementation order and future work boundaries.
