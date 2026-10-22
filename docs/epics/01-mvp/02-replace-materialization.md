# Replace Materialization

Status: **In Progress**
Last updated: 2026-10-22
Epic: 01 MVP
Phase: 02
Related docs: [Roadmap](../../roadmap.md), [Developer API](../../developer-api.md), [Materialization Lifecycle](../../materialization-lifecycle.md), [Schema Inference](../../schema-inference.md)

## Intent

Implement the first complete asset lifecycle: decorate a function, run it,
capture source data, infer the current extraction shape, and transactionally replace
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
evidence, infer the current schema, shape rows, and publish. Keep each phase small enough
to test independently without turning the decorator into a storage policy API.

## Fragment And Staging Contracts

Each run writes `.partial` files under
`<fragment-directory>/asset=<asset-name>/run_id=<run-id>/`. A fragment is
flushed and renamed to `fragment=<number>.ndjson` only when complete.
`_COMPLETE` marks successful source capture. DuckDB receives only completed
fragment paths. Disable Hive partition inference in the shared JSON reader so
storage directory labels cannot become columns or overwrite source values.

`AssquackConfig.ingestion.fragment_target_mb` controls file size independently
of the normalization chunk size. The default is 64 decimal MB (1,000,000 bytes
per MB). Count serialized UTF-8 bytes including newlines and finish a complete
record before rollover at or above the target. Oversized records remain intact;
opening the next file lazily prevents empty trailing fragments.
Native `read_json` scans all completed files
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

## Current-Run Schema Strategy

The [approved scope journal](../../journal/2026-09-08-mvp-scope.md) supersedes
historical schema continuity and detailed observations for MVP.

`schema.replace="current_run"` selects `CurrentRunSchemaStrategy` through the
internal `SchemaStrategy` protocol. Native DuckDB inference over this run's
completed fragments supplies its columns and types. The strategy maps a
heterogeneous `JSON` column to durable `VARIANT` and otherwise preserves native
types. It does not read previous schemas or run observation queries.

Record only the resulting source-column schema in `assquack_meta.schemas`;
leave JSON structure and detailed observations unwritten. Existing catalog
columns, observation helpers and historical records remain for compatibility,
but are outside active materialization.

An empty extraction publishes zero rows with only `_qa_run_id` and
`_qa_loaded_at`. Removed source fields disappear, and scalar or nested types
can change between runs. No unsupported continuation setting is exposed.

## Projection Contract

Build the candidate from current-run projections only. Missing values within
this extraction are handled by DuckDB's native field union. No historical
column or type participates. Candidate creation and schema recording finish
before the publication transaction; failures leave the published table intact.

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
- [x] Replace row-count sizing with a configurable megabyte target and whole-record rollover.
- [x] Infer the current schema across all completed fragments.
- [x] Remove detailed observations and historical reconciliation from active runs.
- [x] Implement current-run schema strategy behind `schema.replace`.
- [x] Build shaped staging from current-run inferred schema.
- [x] Replace the current asset table transactionally.
- [x] Record successful and failed run metadata.
- [x] Derive run IDs from asset identity and the exact persisted UTC runtime.
- [ ] Enforce bounded staging retention after success, failure, and recovery.
- [x] Verify current-run column removal, type changes, nested inference, and empty results.
- [x] Verify source-field preservation and transactional publication rollback/retry.
- [x] Update this status header before handoff.

## Validation

- Run the focused schema/config/fragment tests; do not require the full suite for this step.
- Run Pyright over changed production files using the Poetry interpreter.
- Run Ruff lint over changed production files.
- Run Ruff format checks over changed production files.
- `git diff --check`

## Notes

### Deterministic run identifiers

New run IDs follow the MAD.Prefect `hashlib.md5(...encode()).hexdigest()`
identifier pattern, hashing the UTF-8 encoding of
`f"{asset_id}:{runtime.isoformat()}"`. Capture the UTC datetime once after a
cache miss and bind it into the run insert. Keep the existing run primary key:
duplicate asset/runtime pairs or hash collisions fail before capture and must
not mark an existing run failed.
Claim fragment directories exclusively so orphaned evidence cannot be overwritten.
Existing records and cache-hit IDs remain unchanged. This phase remains
**In Progress** for the previously listed input and retention work.

Independent review of the MD5 derivation found no code defects. All **38 targeted
tests** passed:

```text
poetry run pytest tests/test_run_identity.py tests/test_fragments.py tests/test_cache.py tests/test_schema_replacement.py tests/test_exports.py -q -p no:cacheprovider --tb=short
```

Coverage includes a fixed MD5 example, exact persisted runtime and microseconds, deterministic IDs,
distinct assets/times, duplicate successful and failed runs, cache reuse, orphan
fragment protection, publication rollback and exports. Production and test Ruff
lint/format, targeted Pyright, Markdown links and diff whitespace checks passed.
Pyright and tests needed offline access outside the sandbox for installed
dependencies and local temporary directories. The terminology scan found only
the existing historical observation and its validation note. No full-suite or
live-system checks were run.

This is the MVP spine. Prefer small, explicit components over a broad framework:
decorator contract, materializer, fragment writer, schema strategy, and table
publisher.

### 21 October implementation checkpoint

The current-run schema step is implemented and independently verified. The broader
replace-materialization phase remains **In Progress**.
Retention, remaining input compatibility and exports remain separate work.

Focused production checks passed: Ruff lint/format, Pyright on all six changed
production files, configuration construction and alias serialization, local
Markdown links, and `git diff --check`. Pyright required an unsandboxed offline
run to resolve installed Poetry dependencies. The terminology scan found only
the existing Kubernetes mention in the historical cache/locking observation.
Independent verification passed all **18 targeted tests**:

```text
poetry run pytest tests/test_schema_replacement.py tests/test_schema_inference.py tests/test_fragments.py tests/test_config.py -q --tb=short
```

Coverage includes current-run column removal and type narrowing, sparse nested
objects and arrays, no observation writes, per-asset strategy dispatch, initial
and refresh empty runs, publication rollback with and without a previous table,
and successful retry. The regression found during verification is fixed by
`hive_partitioning=false`: source `asset` and `run_id` values survive, and directory
labels never become extra inferred columns. Independent test-file Ruff,
formatting, Pyright and diff checks passed. No remaining defect was found in this
scope. No full-suite or live-system checks were run.

### Megabyte fragment sizing

Fragment sizing now uses `ingestion.fragment_target_mb`, defaulting to 64 decimal
MB. The environment override and existing per-call config support the same
positive finite target, including fractional MB. Decimal conversion rounds any
fractional byte up without binary floating-point rollover drift. The writer
counts the bytes it writes in binary mode, including the UTF-8 record and LF,
and closes only after a whole record reaches or exceeds the target.

Production Ruff lint/format and targeted Pyright checks passed for configuration,
fragment writing and the materialization pipeline. Pyright used the Poetry
interpreter with offline access outside the sandbox to resolve dependencies.
Documentation links and diff whitespace checks passed. The terminology scan
found only the existing historical observation and its validation note above.
The phase remains **In Progress** for retention and remaining input compatibility.

Independent verification passed **41 targeted tests**:

```text
poetry run pytest tests/test_fragments.py tests/test_config.py tests/test_schema_inference.py tests/test_schema_replacement.py -q -p no:cacheprovider --tb=short
```

Coverage includes exact and fractional-byte MB conversion, UTF-8 and escaped
newline accounting, oversized records, chunk-independent rollover, empty capture,
environment and per-call overrides, and first-run/refresh failure with safe retry.
Test-file Ruff lint/format and targeted Pyright checks passed. Tests used only
synthetic data and temporary local databases; no full-suite or live-system checks
were run. No remaining defect was found in this scope.
