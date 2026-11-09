# Current Timestamp Upsert Workaround

Status: Historical observation; superseded by current publication metadata

This document records an earlier prototype workaround, not the current metadata
contract. Assets now record `first_materialized_at` and `last_materialized_at` on
successful publication; there is no `updated_at` column. See
[Storage Model](../storage-model.md) for current metadata.

## Observed Problem

DuckDB can misbind `current_timestamp` inside an `ON CONFLICT DO UPDATE` clause.
The asset metadata upsert previously used:

```sql
updated_at = current_timestamp
```

DuckDB treated `current_timestamp` as though it were a column on
`assquack_meta.assets` and raised a binder error because that column does not
exist.

## Historical Prototype Behaviour

The attempted insert already supplies `current_timestamp` for `updated_at`.
The conflict update therefore reuses that inserted value:

```sql
updated_at = excluded.updated_at
```

This does not rename the `updated_at` column or create an alias. `excluded`
refers to the row that Assquack attempted to insert.

## Historical Implementation Location

```text
src/assquack/_storage/metadata.py
```

## Historical Requirement

The prototype's materialization contract required the asset metadata
`updated_at` value to be refreshed. It does not prescribe the SQL expression
used by the upsert. Reusing the timestamp from the attempted insert preserves
the required behaviour while avoiding the DuckDB binder failure.
