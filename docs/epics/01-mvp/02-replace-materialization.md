# Replace Materialization

Status: **In Progress**
Last updated: 2026-10-28
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

## Required Table Contract

The 28 October direction requires `asset(table: str, *, mode="replace",
name=None, export=None)`. Accept positional or keyword tables, reject missing or
invalid tables before materialization, and retain explicit table templates.
Remove inferred-table fallback without changing asset naming, binding, cache,
normalization, or export behavior. Validate both call forms, optional exports,
keyword order, invalid tables, and template rebinding with offline tests.

### Required-table implementation validation (28 October 2026)

Implemented the required table-first decorator and direct asset construction,
removed inferred targets, and migrated export declarations without changing their
behavior. Offline coverage verifies positional/keyword equivalence, keyword order,
optional exports, invalid/omitted tables, template rebinding and failure before
extraction or database creation.

- Full offline suite: **245 passed**.
- Ruff lint and formatting for all touched Python files: passed.
- Local Markdown links and `git diff --check`: passed.
- Terminology scan: only existing historical cache/locking references and their
  prior validation notes remain.
- Pyright: no new diagnostics in the changed API or new tests. The API retains
  three baseline diagnostics, verified against `e35a299`: default mapping type,
  the existing ParamSpec/config signature, and ExportSpec-to-request typing.
  Full-project checking also reports existing normalization/query/template issues.

Windows sandbox permissions prevented pytest temporary-directory fixtures and
Pyright dependency discovery; checks were rerun with approved local execution.
No live systems or credentials were accessed. Independent review found that
valid rebinding could be rejected when a previous rendered table was empty.
Construction now validates the original template; execution still validates the
resolved target. Regression coverage includes empty defaults, cache-first copies,
and repeated rebinding for qualified and unqualified table templates.
The focused contract/resolution suite passes **19 tests** after the fix.
Independent re-review approved the fix with no remaining in-scope findings.
The reviewer independently passed **247 tests**, Ruff lint, fix-file formatting,
Markdown links, and `git diff --check`.
This phase remains **In Progress** for the separate documented input-support work.

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
4. Ignore `.partial` files during inference; retain them with failed-run evidence.
5. Drop older fragment directories during post-run and next-call cleanup.

Derive keep/drop decisions from `assquack_meta.runs` by asset ID, status and
timestamps. Shared asset-name folders do not imply shared retention. Use run ID
only to break exact timestamp ties. After acquiring the database writer lock,
mark the current asset's earlier running attempts interrupted/failed before
cache lookup. Cleanup never selects records still marked running.

Remove only flat known fragment files and the completion marker from exact
catalog-owned run directories under the configured root. Reject unsafe IDs,
symlink/reparse traversal and unexpected content; do not scan or delete orphans.
Leave run metadata, published tables and exports intact. Log cleanup errors
without changing successful publication or masking extraction failure. Retry
starts a fresh extraction; retained evidence is diagnostic only.

The `runs.fragments_owned` flag tracks directory ownership;
new attempts start unowned. Set it only after exclusive directory creation
succeeds. A failed claim or interruption before recording ownership leaves that
directory untouched, even after its run falls outside retention.

## Current-Run Schema Strategy

The [approved scope journal](../../journal/2026-09-08-mvp-scope.md) supersedes
historical schema continuity and detailed observations for MVP.

`schema.replace="current_run"` selects a fresh `CurrentRunSchemaStrategy` from
an immutable internal registry. Each instance binds its run connection and
fragment supplier. Its argument-free `resolve()` obtains completed fragments,
performs native DuckDB inference and maps heterogeneous `JSON` columns to
`VARIANT`. The pipeline neither supplies an inferred schema nor branches on
strategy behavior. There is no public custom-strategy registration API.

Schema-history persistence, readers and observation helpers are removed.
New catalogs contain only assets, runs and exports; existing database tables
and records are not deleted. No compatibility shim or migration is introduced.
An empty extraction publishes zero rows with only `_qa_run_id` and
`_qa_loaded_at`. Removed source fields disappear, and scalar or nested types
can change between runs. No unsupported continuation setting is exposed.

## Projection Contract

Build the candidate from current-run projections only. Missing values within
this extraction are handled by DuckDB's native field union. No historical
column or type participates. Candidate creation finishes
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
- [x] Verify bounded fragment retention after success, failure, and interruption.
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

### Fragment retention

Fragment retention is implemented and independently reviewed. Keep the latest
successful attempt and three latest failed attempts per asset identity, using
catalog timestamps and run ID only for exact ties. Recover interrupted attempts
under the existing exclusive writer lock; retries always extract afresh.

Review exposed an orphan-claim deletion bug and a DuckDB bootstrap behavior that
reset ownership when repeating `ADD COLUMN IF NOT EXISTS ... DEFAULT true`.
Both are fixed: new attempts claim fragments only after exclusive directory
creation. The legacy column migration and its upgrade test were removed on
26 October: this unreleased MVP does not upgrade earlier catalog layouts.
Filesystem deletion is confined to validated flat run directories; cleanup
failures log warnings without changing publication or masking source errors.

Independent verification passed **32 targeted tests**:

```text
poetry run pytest tests/test_fragment_retention.py tests/test_run_identity.py tests/test_cache.py tests/test_writer_lock.py tests/test_exports.py -q -p no:cacheprovider --tb=short
```

Coverage includes retention counts and timestamp ties, shared asset-name folders,
configured roots, UUIDs, orphan preservation across
reopen, redirected directories, unknown contents, cancellation/fresh retry,
interrupted recovery on cache hits, cleanup errors, writer locks and exports.
Targeted production/test Pyright and Ruff lint/format, Markdown links and diff
checks passed. The terminology scan found only the existing historical locking
observation and its prior validation note. Tests and type checks used offline
access to installed dependencies and temporary local files. No full-suite or
live-system checks were run. No outstanding finding remains in this scope.

This phase remains **In Progress** for the remaining documented input support.

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

## Internal schema registry — 26 October 2026

The EXT-05 slice is complete: framework-owned selection, argument-free
`resolve()`, strategy-owned inference, and removal of unused schema persistence
and observation machinery. See [EXT-05 validation](06-extensibility-defects.md#ext-05-schema-strategy-selection-and-context)
for the 173-test full-suite result, subsequent five-test inference run, lint,
format, documentation checks and unrelated Pyright limitations. This phase
remains In Progress for its other outstanding work.
