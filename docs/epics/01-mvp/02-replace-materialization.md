# Replace Materialization

Status: **Planned**
Last updated: 2026-05-08
Epic: 01 MVP
Phase: 02
Related docs: [Roadmap](../../roadmap.md), [Developer API](../../developer-api.md), [Materialization Lifecycle](../../materialization-lifecycle.md), [Schema Inference](../../schema-inference.md)

## Intent

Implement the first complete asset lifecycle: decorate a function, run it,
stage observed data, infer a conservative shape, and transactionally replace
the current DuckDB table.

## Scope

Included:

- `@asset(...)` definition and callable behavior.
- Sync and async asset function execution.
- Supported batch normalization into raw staging.
- `_qa_payload VARIANT` raw evidence staging.
- Shaped staging with promoted typed columns.
- `replace` mode table swap and run metadata update.
- Bounded staging retention for successful and failed runs.
- `AssquackResult` creation after successful materialization.

Excluded:

- Incremental `append`, `merge`, and `snapshot` modes.
- Advanced promotion authoring APIs.
- Export writes.

## Architecture Notes

Treat materialization as a pipeline with explicit phases: normalize, stage raw
evidence, observe schema, shape rows, and publish. Keep each phase small enough
to test independently without turning the decorator into a storage policy API.

## Staging Table Contracts

Raw staging always keeps the observed payload in `VARIANT`:

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

Shaped staging starts with reserved metadata columns, adds promoted typed asset
columns, and retains `_qa_payload VARIANT` only when policy requires raw,
bronze, audit, or discovery access.

## Staging Retention Contract

After each run, apply retention per asset:

1. Keep the raw staging table for the latest successful run.
2. Keep raw and shaped staging tables for the latest three failed runs.
3. Drop shaped staging for successful runs after the current table is published.
4. Drop transient JSON or chunk helper tables at the end of the chunk lifecycle.
5. Drop older `assquack_stage` tables during post-run cleanup and maintenance
   cleanup.

The cleanup step should derive keep/drop decisions from `assquack.runs` and the
staging table naming convention. It must not drop staging for a run still marked
`running` unless a later recovery policy has first marked that run stale or
failed.

## Schema Observation Queries

For JSON-origin chunks, capture compact structure evidence:

```sql
INSERT INTO assquack.schemas (
  asset_id,
  run_id,
  schema_json,
  json_structure,
  created_at
)
SELECT
  $asset_id,
  $run_id,
  NULL,
  json_group_structure(payload_json),
  current_timestamp
FROM assquack_stage.json_chunk_<run_id>;
```

Collect path-level evidence with `json_tree`:

```sql
INSERT INTO assquack.schema_observations (
  asset_id,
  run_id,
  path,
  observed_type,
  present_count,
  null_count,
  total_count,
  first_seen_at,
  last_seen_at
)
SELECT
  $asset_id,
  $run_id,
  jt.fullkey AS path,
  jt.type AS observed_type,
  count(DISTINCT c._qa_row_number)
    FILTER (WHERE jt.type IS NOT NULL) AS present_count,
  count(DISTINCT c._qa_row_number)
    FILTER (WHERE jt.type = 'NULL') AS null_count,
  $chunk_row_count AS total_count,
  current_timestamp,
  current_timestamp
FROM assquack_stage.json_chunk_<run_id> c,
     json_tree(c.payload_json) AS jt
GROUP BY jt.fullkey, jt.type;
```

## Projection Contract

Projection should tolerate missing and malformed values while recording failed
casts as evidence:

```sql
SELECT
  _qa_run_id,
  _qa_loaded_at,
  _qa_payload,
  try_cast(variant_extract(_qa_payload, '$.id') AS VARCHAR) AS id,
  try_cast(variant_extract(_qa_payload, '$.amount') AS DECIMAL(18, 2)) AS amount
FROM assquack_stage.raw_<asset_id>_<run_id>;
```

## Publish Contract

The replace-mode swap should be implemented as transactional DuckDB DDL/DML. One
acceptable shape is:

1. Create a run-scoped next table in the target schema from shaped staging.
2. Inside the same transaction, drop or rename the previous current table.
3. Rename the next table to the stable current table name.
4. Apply staging retention cleanup after the swap succeeds.

## Implementation Checklist

- [ ] Implement strongly typed public asset decorator contracts.
- [ ] Normalize supported returned and yielded inputs.
- [ ] Write raw staging rows with `_qa_payload VARIANT`.
- [ ] Record schema observations while loading chunks.
- [ ] Build shaped staging from conservative effective schema.
- [ ] Replace the current asset table transactionally.
- [ ] Record successful and failed run metadata.
- [ ] Enforce bounded staging retention after success, failure, and recovery.
- [ ] Add tests for missing fields, type drift, failed casts, and empty results.
- [ ] Update this status header before handoff.

## Validation

- `pytest`
- type-check command selected during bootstrap
- `git diff --check`

Replace placeholder validation commands with concrete commands once Phase 00
chooses the project tooling.

## Notes

This is the MVP spine. Prefer small, explicit components over a broad framework:
decorator contract, materializer, staging writer, schema observer, and table
publisher.
