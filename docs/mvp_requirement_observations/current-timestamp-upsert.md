# Current Timestamp Upsert Workaround

Status: Working MVP requirement observation

This document records prototype behaviour and does not change the contracts in
the canonical documentation.

## Observed Problem

DuckDB can misbind `current_timestamp` inside an `ON CONFLICT DO UPDATE` clause.
The asset metadata upsert previously used:

```sql
updated_at = current_timestamp
```

DuckDB treated `current_timestamp` as though it were a column on
`assquack_meta.assets` and raised a binder error because that column does not
exist.

## MVP Behaviour

The attempted insert already supplies `current_timestamp` for `updated_at`.
The conflict update therefore reuses that inserted value:

```sql
updated_at = excluded.updated_at
```

This does not rename the `updated_at` column or create an alias. `excluded`
refers to the row that Assquack attempted to insert.

## Current Prototype Location

```text
src/assquack/_storage/metadata.py
```

## Compatibility With Canonical Docs

The canonical materialization contract requires the asset metadata
`updated_at` value to be refreshed. It does not prescribe the SQL expression
used by the upsert. Reusing the timestamp from the attempted insert preserves
the required behaviour while avoiding the DuckDB binder failure.
