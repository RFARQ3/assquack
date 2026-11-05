# Default connection and direct fragment writing

Status: **Complete**
Last updated: 2026-11-05
Epic: 01 MVP
Phase: 07
Related docs: [Developer API](../../developer-api.md), [Configuration](../../configuration.md), [Storage](../../storage-model.md)

## Native DuckDB query direction (5 November 2026)

The approved simplification supersedes every earlier SQL qualification and
function-value rule recorded below. Delete the custom qualifier, parser/serializer
normalization, general `assquack.query` API and task-local `use_config` machinery.
DuckDB resolves developer SQL and parameters unchanged, using its default catalog
and search path. No special multi-database or function-value rules remain.

Import initializes and selects the configured database when `ASSQUACK_HOME` or
`ASSQUACK_DATABASE_PATH` is present in the process environment. Attachment names,
resource settings and export settings alone do not create a disk database.
Python configs remain lazy until attach or asset use. The first Assquack setup
selects the default once; later attachments preserve native USE/search_path choices.
Manual attach and environment initialization bootstrap Assquack metadata.

Result queries follow the FROM-first decision below, return native relations and
accept bound parameters. Asset queries execute the asset's normal cache policy
and then delegate to result queries. Do not reinstate SQL rewriting.

Namely moves general reads to `duckdb.query` and retains its Python config and
explicit attach demonstration. Its selection/publication business logic stays
unchanged. No dependency publication or pin changes belong to this task.

## FROM-first decision (5 November 2026)

Rowan explicitly chose to depart from Maitland's original MVP `FROM data` design.
A shared alias introduces collisions and silent native name precedence. Instead,
Assquack supplies `FROM "database"."schema"."table"` for the known result table,
followed by the caller's SQL unchanged. This follows MAD.Prefect's omitted-FROM
usage while replacing its Python source reference with a qualified physical table.
No alias binding, registration, collision guard, SQL parser or qualification layer
belongs in this implementation.

The contract supports omitted SQL and native bound parameters. Full SQL, including
a leading CTE, belongs in native DuckDB queries over the returned relation.
Document both Python-name lookup through `duckdb.query` (possible unused-variable
warnings) and `relation.query(alias, sql)` (explicit Python use, extra alias
argument). The downstream developer chooses; lint suppression is not mandatory.

Implementation resumed on 5 November in the visible worker/reviewer chats.
Source, tests and downstream examples now omit `FROM data`.
Earlier test evidence below does not validate this new contract. Fresh validation
and review are recorded under `.tmp/from-first-query`.

## FROM-first implementation validation (5 November 2026)

The helper prefixes the quoted physical source and appends SQL unchanged, using
native positional or named parameters. Omitted SQL returns a lazy native relation.
No custom SQL qualification, serialization, general query or config scope remains.
Namely's five affected asset/result query strings were updated in three files;
business logic, lint policy, dependency pins and publication remain unchanged.

- Full offline suite: **393 passed, no skips**, Python 3.11 / DuckDB 1.5.6.
- Namely synthetic report history: **12 passed**.
- Ruff lint/format, Markdown links and diff whitespace pass.
- Pyright: **five baseline diagnostics**, freshly reproduced against HEAD.
- Test-only runner uses ordinary scratch directories because pytest's temporary
  directory ACLs fail in this Windows sandbox. Its Windows spawn guard was fixed
  before the successful full run. No runtime workaround was introduced.
- Independent review approved with no actionable findings or unresolved tradeoffs.
  Its fresh full suite passed **393 tests** and Namely passed **12 tests**.
- Shared evidence: `.tmp/from-first-query`, including `review.md` and
  `decisions-needed.md`. Phase completed 5 November 2026.

## Earlier validation checkpoint (before the FROM-first decision)

Native-default/API/downstream changes are implemented. Result alias collision
policy remains reserved for user mediation, so this phase is not complete.
A scratch candidate using Python replacement binding plus a native lookup guard
passed 383 offline tests with DuckDB 1.5.6, followed by 17 focused native-default
and alias tests. The guarded collision policy is proposed, not approved or applied.
The actual result query seam remains unfinished pending that decision.
All 12 synthetic Namely history tests passed with the local scratch source;
no live systems or dependency pin changes were used.

Current lint/format, doc links and whitespace checks pass for Assquack. Candidate
Pyright has the same seven diagnostics as the captured baseline in the same
environment (five API/template issues and two existing url2pathname deprecation
reports). Namely lint retains native-SQL variable false positives and its existing
broad exception-handler warning; its two touched Python files pass formatting.

## Implementation boundaries

Keep named attachments, engine-setting conflict checks, canonical asset tables,
publication transactions, direct fragment capture, exports and retention.
Asset, staging and metadata SQL still uses generated qualified identifiers; this
is internal storage SQL, not rewriting developer SQL.

No hidden agents, live systems, credentials, branch/worktree changes or commits.
The implementation and independent review are in the visible worker/reviewer pair.

## Validation

Cover import-time synthetic environment setup and reopen, resource-only imports,
first/default attachment behavior, native USE/search_path, native function values,
qualified FROM-first sources and parameters, both native composition avenues,
lazy results, cross-database composition, cache
policy and the synthetic Namely report-history suite. Run offline tests, lint,
format, type, doc links and whitespace checks. Keep baseline limitations explicit.

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
