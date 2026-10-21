# Replace Materialization

Status: **In Progress**
Last updated: 2026-09-10
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
- Supported batch normalization into local fragments.
- Bounded local NDJSON fragment capture.
- Native DuckDB JSON inference across the complete fragment set.
- Shaped staging with promoted typed columns.
- `replace` mode table swap and run metadata update.
- Bounded fragment retention for successful and failed runs.
- `AssquackResult` creation after successful materialization.

Excluded:

- Incremental `append`, `merge`, and `snapshot` modes.
- Advanced promotion authoring APIs.
- Export writes.

## Architecture Notes

Treat materialization as a pipeline with explicit phases: normalize, stage raw
evidence, observe schema, shape rows, and publish. Keep each phase small enough
to test independently without turning the decorator into a storage policy API.

## Fragment And Staging Contracts

Each run writes `.partial` files under
`<fragment-directory>/asset=<asset-name>/run_id=<run-id>/`. A fragment is
flushed and renamed to `fragment=<number>.ndjson` only when complete.
`_COMPLETE` marks successful source capture. DuckDB receives only completed
fragment paths.

`AssquackConfig.ingestion.fragment_max_rows` controls file size independently
of the normalization chunk size. Native `read_json` scans all completed files
with full sampling and field union before the shaped candidate is built.

Shaped staging starts with reserved run metadata and adds the effective typed
asset columns. Genuinely heterogeneous columns use `VARIANT`; a whole-row
`_qa_payload` is not mandatory.

## Staging Retention Contract

After each run, apply retention per asset:

1. Keep fragments for the latest successful run.
2. Keep fragments for the latest three failed runs.
3. Drop shaped staging for successful runs after the current table is published.
4. Ignore `.partial` files during inference and recovery.
5. Drop older fragment directories during post-run and maintenance cleanup.

The cleanup step should derive keep/drop decisions from `assquack_meta.runs` and
the fragment directory naming convention. It must not drop evidence for a run
still marked `running` unless recovery has first marked that run stale or failed.

## Schema Observation Queries

For each completed fragment, capture compact structure evidence:

```sql
SELECT json_group_structure(json)
FROM read_json_objects(
  $fragment_path,
  format = 'newline_delimited'
);
```

Collect path-level evidence with `json_tree`:

```sql
SELECT
  tree.fullkey AS path,
  tree.type AS observed_type,
  count(*) AS present_count
FROM read_json_objects(
       $fragment_path,
       format = 'newline_delimited'
     ) AS source,
     json_tree(source.json) AS tree
WHERE tree.fullkey <> '$'
GROUP BY tree.fullkey, tree.type;
```

## Projection Contract

The candidate table comes directly from the native reader's combined schema.
Projection should tolerate missing historical fields and malformed values:

```sql
SELECT
  $run_id AS _qa_run_id,
  current_timestamp AS _qa_loaded_at,
  try_cast(id AS VARCHAR) AS id,
  try_cast(amount AS DECIMAL(18, 2)) AS amount,
  CAST(NULL AS VARCHAR) AS previously_observed_field
FROM read_json($fragment_paths, union_by_name = true, sample_size = -1);
```

## Publish Contract

The replace-mode swap should be implemented as transactional DuckDB DDL/DML. One
acceptable shape is:

1. Create a run-scoped next table in the target schema from shaped staging.
2. Inside the same transaction, drop or rename the previous current table.
3. Rename the next table to the stable current table name.
4. Apply staging retention cleanup after the swap succeeds.

## Implementation Checklist

- [x] Implement strongly typed public asset decorator contracts.
- [ ] Normalize every documented returned and yielded input.
- [x] Write bounded local NDJSON fragments with atomic completion.
- [x] Infer the current schema across all completed fragments.
- [x] Record schema observations from each completed fragment.
- [x] Build shaped staging from conservative effective schema.
- [x] Replace the current asset table transactionally.
- [x] Record successful and failed run metadata.
- [ ] Enforce bounded staging retention after success, failure, and recovery.
- [ ] Add tests for missing fields, type drift, failed casts, and empty results.
- [x] Update this status header before handoff.

## Validation

- `poetry run pytest`
- `poetry run pyright`
- `poetry run ruff check .`
- `poetry run ruff format --check .`
- `git diff --check`

## Notes

This is the MVP spine. Prefer small, explicit components over a broad framework:
decorator contract, materializer, fragment writer, schema observer, and table
publisher.
