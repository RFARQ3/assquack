# Default connection and direct fragment writing

Status: **Complete**
Last updated: 2026-11-03
Epic: 01 MVP
Phase: 07
Related docs: [Developer API](../../developer-api.md), [Configuration](../../configuration.md), [Storage](../../storage-model.md)

## Approved outcome

Final resolution rule approved 3 November 2026: one attached user database allows
`table` or `schema.table`; multiple user databases require `database.schema.table`
for physical references regardless of selected config. Internal/default memory
catalogs do not count. CTEs, Python replacement scans and asset `data` remain
statement-local. This supersedes the earlier scoped precedence and shorthand
rules below. Recursive anchors must bind before the recursive name is visible.

Use DuckDB's actual default connection for native query results, direct JSON
capture and publication. Preserve per-asset configuration and multiple database
destinations. This approved direction supersedes the earlier single-database
proposal in this file. Implementation is authorised; no commits or live tests.

Both asset and general queries return `DuckDBPyRelation`. Two results, including
results from different configured databases, compose through Python variables in
`duckdb.query()`. Native methods keep their native signatures. No wrapper,
temporary Parquet transport or task-specific default connection is involved.

## Implementation decisions

- Expose `attachment_name` (default `assquack`) and synchronous `config.attach()`.
  Register matching names/paths idempotently; reject alias/path conflicts.
  Config creation and decoration perform no I/O. First asset use auto-attaches;
  otherwise attach explicitly before fully qualified SQL.
  Fully qualify asset, staging and metadata tables with that destination.
- Keep one process-lifetime default connection. Never close it after an operation.
  External callers must not replace or close it while Assquack is active.
- The first configuration establishes engine threads, memory and temporary
  directory. Later conflicting explicit engine settings fail; omitted settings inherit
  the established engine values. Preserve explicit field tracking. Destinations,
  fragment sizing/locations, schema policy and exports remain per-config.
- Serialize complete synchronous database operations and publication transactions.
  Never await user code inside a transaction or hold a database lock across source
  execution. Track active capture runs so nested/concurrent calls do not recover
  live runs as abandoned. A transaction writes one destination database only.
- Capture native relations with direct JSON COPY on the default connection.
  Foreign-connection relations fail with an actionable error. pandas and Arrow
  inputs create native relations on the default connection directly.
- Empty input registers no data fragments. Empty COPY output is deleted during
  capture; `_COMPLETE` is a control marker, not a data fragment.
- General query SQL is trusted application SQL. SELECT validation is not a
  security sandbox. No long-lived read-only transaction or snapshot is promised.
- Arbitrary direct multi-threaded use of DuckDB's global connection is unsupported;
  the Assquack lock cannot coordinate external calls that bypass it.

## Validation

Cover native replacement scans, cross-catalog joins, per-destination metadata,
rollback, cancellation, nested async generators and child tasks, lazy lifetimes,
settings conflicts, decimals, empty inputs, recombination, exports and retention.
Run the full offline suite, lint, typing, doc links and whitespace checks.

The [capture report](../../../benchmarks/capture-20261102-report.md) and
[methodology](../../../benchmarks/capture-20261102-methodology.md) already contain
the full benchmark matrix. Direct capture and historical parent
`5ab0e2192422b3bce3df58fd860d5c6dd4ba5007` were effectively tied (median relative
speed difference 0.4%); the Parquet bridge was worse and corrupted decimals.
All 48 empty checks passed. Do not rerun the long matrix or claim the default
connection is inherently faster than historical direct capture.

## Earlier review and completion

Completed 3 November 2026. Independent review found no unresolved actionable
findings after fixes for catalog spelling and quoted-name resolution, active-run
tracking across destinations, temporary capture-name collisions, and cleanup.

- Full offline suite: **387 passed, no skips** (independent run: 34.93 seconds).
  DuckDB 1.5.2; cached pandas 3.1.0 and Arrow 26.0.0 were exercised.
- Ruff check and format check passed; documentation links and `git diff --check`
  passed. Mermaid changes were reviewed by inspection. Terminology scan found
  only explicitly historical/reference notes.
- Pyright: five existing errors in `_api.py` and `_templates.py`. The same
  configuration against the original HEAD source reproduced all five diagnostics;
  this implementation adds none. These unrelated type-contract issues remain.
- Default pytest temporary directories failed under Windows sandbox ACLs. An
  ordinary-directory fixture in the local validation runner completed the full
  suite within the sandbox. The unsandboxed approval attempt timed out and did
  not execute. No permission changes, live systems, customer data or secrets.
- Basic Memory CLI context lookup was unavailable because its log-file location
  was not writable in the sandbox. Repository guidance and the approved contract
  supplied the implementation context.
- Validation runner and typing comparison artifacts are retained locally under
  `.local/default-connection-validation-20261103/` (ignored). The long benchmark
  matrix was not rerun; its existing evidence remains unchanged.

Known operating limits are documented in the linked developer pages: lazy results,
process-lifetime attachment file handles, explicit foreign-relation rejection,
scoped table-reference rewriting, and unsupported direct concurrent external use
of DuckDB's global connection. No commits, pushes, branch or worktree changes.


## Earlier correction: direct capture and scoped table binding

Reopened 3 November 2026 for two approved corrections. Capture must use Python
replacement scans directly in COPY, with no SQL registration/view or eager probe
of the input relation. Scoped queries must retain DuckDB catalog/table shorthand
and bind query_table string/list arguments and parameters to the selected catalog.
The single-process shared-default baseline, attachment/API/settings contract,
publication coordination, exports and retention remain unchanged.

Previous completion evidence above predates these corrections.

- Capture now uses the original relation through a disposable Python replacement
  scan binding. Catalog inspection prevents table/view name collisions. A
  side-effecting regression observes views during COPY and proves no input
  pre-execution; completed fragments and retained lazy relations remain usable.
- Scoped binding handles catalog/table shorthand, quoted and schema-qualified
  names, and literal or parameterized `query_table` string/list arguments. CTEs,
  Python replacement scans, reused parameter values, and explicit user macros
  retain their native meaning. Computed table-name expressions fail with guidance
  to supply string/list parameters.
- Synthetic snapshot metadata tests exercise schema/table lists and
  `union_by_name` through both general and asset queries. No customer systems
  or data were accessed. The shared JSON reader is unchanged; its large-record
  regression remains in the full suite.
- Final independent full suite: **417 passed, no skips** in 29.69 seconds,
  including the explicit-macro regression. Doer full suite passed 416 tests
  before that regression; the subsequent scoped query run passed all 29 tests.
- Ruff check/format, documentation links and diff whitespace checks pass.
  Strict Pyright checks using relative include/strict paths reproduce the same
  five diagnostics against current and original HEAD source, with no new errors.
- Independent review approves both corrected mechanisms with no unresolved
  actionable findings. Phase completed 3 November 2026. The separately requested
  emptiness-probe benchmark is evidence-only and does not change production
  behavior; its quiet timing window began after both full-suite runs finished.

## Final resolution rule correction

The earlier query review is superseded by this correction. One user attachment
allows `table` and `schema.table`; multiple attachments require full three-part
physical names, even when unique. This applies with or without a config scope.
Catalog inventory excludes system/temp/default memory, including when ordinary
in-memory user databases are attached. There is no search across catalogs or
config-based read fallback. Asset configuration still determines publication.

Recursive CTE anchors bind in outer scope; only the recursive branch sees its own
CTE name. Both Python and file replacement-scan exemptions check physical-name
collisions, including memory/temp/views, so they cannot bypass qualification and
read an unrelated table. DuckDB may still bind a genuine Python relation when a
qualified physical target is absent. Qualified `query_table` functions/macros
fail clearly, directing callers to unqualified `query_table` with qualified table
arguments. String/list parameters remain unchanged when reused elsewhere.

New tests cover one/multiple attachments, absent and unique physical names,
qualified joins and snapshot lists, native locals, normal/recursive CTEs, asset
`data`, write destinations and retained lazy relations. All 49 focused resolution
tests pass. Independent final full validation passed **437 tests, no skips**, in
34.15 seconds. Fresh independent mechanism review found no remaining actionable
findings. Ruff, formatting, documentation links and diff checks pass; strict
Pyright retains the same five baseline diagnostics. Existing capture,
large-record JSON recovery and benchmark artifacts are unchanged by this
resolution-rule correction. No benchmark rerun, commits or live access.
