# Should Assets Require An Explicit Schema?

Status: Open MVP question

## Context

The canonical docs require a sanitized schema and table for every durable asset
but do not define what happens when an asset supplies no schema. The prototype
previously invented a shared `assquack_assets` schema without documenting that
decision.

The current prototype instead uses DuckDB's standard `main` schema and emits a
`MissingAssetSchemaWarning`. A qualified `table="schema.table"` value continues
to use its declared schema.

## Question

Is `main` with a visible warning an acceptable fallback for the MVP, or should
materialization fail when an asset has no explicitly defined schema?

## Consequences To Decide

- A `main` fallback keeps simple assets runnable but permits unrelated assets to
  share one namespace.
- An error makes table placement explicit but requires every asset to define a
  schema before materialization.
- The canonical requirement to derive names from asset identity may instead
  require an approved schema-derivation convention.

## Current Prototype Location

```text
src/assquack/_api.py
src/assquack/_errors.py
src/assquack/_storage/tables.py
tests/test_table_resolution.py
```
