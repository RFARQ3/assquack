# Query And Cache

Status: **In Progress**
Last updated: 2026-10-29
Epic: 01 MVP
Phase: 03
Related docs: [Roadmap](../../roadmap.md), [Developer API](../../developer-api.md), [Materialization Lifecycle](../../materialization-lifecycle.md), [Storage Model](../../storage-model.md)

## Intent

Expose the current DuckDB table through a small result/query surface and make
cache-first reads depend on Assquack metadata rather than exported files.

## Scope

Included:

- `.query(sql=None, params=None)` for assets and results.
- Stable `data` relation alias for result queries.
- `.exists()` based on current table and latest successful run.
- `cache_first()` and TTL behavior from Assquack metadata.
- Decorator `cache_expiration` defaults, preserving fluent cache overrides.
- Parameter binding for query values.

Excluded:

- External cache stores.
- File-existence cache checks as the source of truth.
- Export-required cache policy beyond manifest lookup.

## Architecture Notes

Query and cache should read from the same metadata and current-table contracts
used by materialization. Keep SQL parameter binding centralized so convenience
methods do not create alternate query paths.

Cache validity depends only on the successful materialization, current table and
TTL. On a cache hit, retry exports without a matching run/path/fingerprint
manifest under the existing writer lock. Preserve partial successes and the
original run identity and publication timestamp; export retries do not renew TTL.

Decorator TTLs use the existing request/cache path: `None` and zero leave normal
calls uncached, positive durations enable reuse, and negative durations fail at
declaration, consistent with Assquack's fluent validation. `cache_first()` keeps
its existing unlimited default and explicit TTL override on an immutable copy.
Validate cache misses, hits, expiry, argument binding and override isolation with
synthetic rows and temporary DuckDB databases.

## Implementation Checklist

- [ ] Implement `AssquackResult` table reference behavior.
- [ ] Implement result and asset `.query(...)`.
- [ ] Implement `.exists()`.
- [x] Implement `cache_first()` against metadata and current table presence.
- [x] Add decorator TTL defaults with binding and immutable override coverage.
- [ ] Add query parameter binding tests.
- [x] Add cache-hit, cache-miss, stale-cache, and missing-table tests.
- [x] Separate cached materialization reuse from export completion, with offline
  retry, partial-success, fingerprint and TTL regression coverage.
- [x] Update this status header before handoff.

## Validation

- `poetry run pytest`
- `poetry run pyright`
- `poetry run ruff check .`
- `poetry run ruff format --check .`
- `git diff --check`

## Notes

The query surface should stay small. Add convenience methods only when they
make common developer workflows clearer without expanding the storage model.


### Cache/export separation validation (28 October 2026)

- Full offline suite: 231 passed, using an explicit temporary directory and
  sandbox escalation because Windows denied pytest temporary-directory access.
- Ruff lint, documentation links and `git diff --check` pass.
- Pyright reports 13 existing errors in untouched `_api.py`, `normalize.py`,
  `_query.py` and `_templates.py`; no diagnostics in changed files.
- Global formatting has existing failures in destination implementation files,
  `test_destination_resolution.py` and the query-connections observation example.
- Terminology scan retains only the historical Kubernetes cache/locking note
  and its existing epic explanation.

This cache/export change is implemented; the phase remains In Progress for the
unchecked query work above. Independent review approved the change with no
actionable in-scope defects: 57 scoped tests and a synthetic multi-destination
retry scenario passed, including run identity and TTL preservation.

### Decorator cache defaults validation (29 October 2026)

MAD.Prefect's decorator forwards `cache_expiration` into asset options. Its
callable reuses only positive TTLs and expires at the exact TTL boundary.
Assquack now accepts the same decorator option using its metadata/current-table
cache path. It retains its existing negative-TTL rejection and unlimited
`cache_first(None)` semantics (MAD.Prefect uses 9999 days for that helper).
Zero TTL always refreshes, including future stored timestamps.

- Full offline suite: 275 passed, 8 skipped because optional pandas/pyarrow are
  unavailable. Temporary-directory permissions required approved escalation.
- Independent review: 76 passed, 8 optional-dependency skips; no blocking findings.
- Ruff lint, changed Python file formatting, Markdown links and diff checks pass.
- Pyright with the Poetry interpreter reports 7 pre-existing diagnostics on
  unchanged lines in `_api.py`, `_query.py` and `_templates.py`.
- Repository-wide formatting reports 8 unchanged files; the sandbox also cannot
  traverse the reviewer's temporary test directory. Existing terminology matches
  remain in the historical cache/locking note and explanatory epic notes.
- Namely's two global two-hour cache assignments were moved into decorators;
  source syntax and whitespace checked only, with no imports or live execution.

This addition is complete; the phase remains In Progress for the unchecked
query work above.

## Asset options and attached config

### Table-derived default names (29 October 2026)

Implemented: retain an omitted name in the private template state and resolve
it from the table template during binding and execution.
Table option copies update default-derived names, retain explicit names, and
`name=None` restores the table-derived default. Validate immutable chaining,
execution overrides, metadata and cache identity with offline synthetic rows.
No compatibility shim or metadata migration is required for this pre-release API.

- Full offline suite: 295 passed, 8 optional pandas/pyarrow skips; Windows
  temporary-directory permissions required approved escalation.
- Ruff source/test lint, changed-file formatting, Markdown links and diff checks
  pass. Terminology matches remain confined to the existing historical note
  and epic explanations.
- Pyright with the Poetry interpreter reports the same seven pre-existing
  diagnostics on unchanged logic in `_api.py`, `_query.py` and `_templates.py`.
- Updated export and fragment path expectations; shared-name retention fixtures
  now declare their names explicitly. Namely was not edited or executed.

This naming change is complete; the phase remains In Progress for the unchecked
query work above.

Implemented 29 October 2026. The agreed
[public API goal](../../developer-api.md#asset-config-and-option-copies)
adds decorator `config=` and immutable `with_options()` customisation. The phase
remains In Progress for the unchecked query work above.

- [x] Accept an optional default `AssquackConfig` in the asset decorator.
- [x] Add typed `with_options()` parameters matching decorator settings, including
  config; retain omitted options and distinguish explicit nullable resets.
- [x] Preserve the wrapped function, bound arguments and original templates when
  copying; apply changed templates correctly to already-bound arguments.
- [x] Preserve attached config through `with_arguments()`, `cache_first()` and
  other option copies without modifying the original asset or shared config.
- [x] Resolve config consistently for execution, asset queries and existence
  checks: explicit call config, then attached config, then normal defaults.
- [x] Verify independent config objects direct assets to independent database
  paths; no environment-label field or new config-provider abstraction.
- [x] Test option resets, cache policy consistency, chaining in either order,
  template resolution and absence of execution during configuration.
- [x] Update Namely to attach its shared config at asset definition rather than
  repeat it at every call, retaining explicit overrides only where intended.
- [x] Update runnable examples after implementation; do not add `with_config()`.

Validation uses offline synthetic rows and temporary databases. Namely source
now requires a published Assquack version containing this API; dependency pins
remain unchanged pending review. No Namely pipeline or live system was executed.


### Asset options validation (29 October 2026)

- Full offline suite: 291 passed, 8 optional pandas/pyarrow skips. Windows
  temporary-directory permissions required approved escalation.
- Independent review approved the implementation after fixing validation of
  partially rendered table templates; 65 scoped tests passed, 8 optional skips.
- Ruff lint across source/tests and changed-file formatting pass. Broad formatting
  still reports seven unchanged source/test files.
- Pyright with the Poetry interpreter reports the same seven pre-existing
  diagnostics on unchanged lines in `_api.py`, `_query.py` and `_templates.py`.
- Markdown links and both repository diff checks pass. Terminology matches remain
  limited to the historical cache/locking observation and its existing notes.
- Four changed Namely source files pass static parsing. Its snapshot factory also
  passes isolated synthetic attached-config and explicit-override checks using
  temporary databases; no Namely API modules or pipeline were executed.
- Existing local config, ignore, editor settings and dependency edits were
  preserved. No implementation commit, push or dependency-pin change was made.

## General database query implementation

Approved 29 October 2026 from Rowan's screenshot and explicit implementation
request. The public contract lives in
[General database queries](../../developer-api.md#general-database-queries).
This MVP addition is implemented and independently reviewed. The overall phase
remains In Progress for the other unchecked work.

- [x] Export typed async `assquack.query(sql, params=None, *, sources=None)`.
- [x] Explicit alias-to-config sources replace implicit sources; reject an empty
  mapping and invalid aliases/configs clearly, with safe identifier quoting.
- [x] Use the executing asset's effective config when sources are omitted,
  including execution overrides and sync/async generator iteration.
- [x] Restore nested contexts and isolate concurrent assets; reset context after
  success, failure and cancellation. Direct child asset config selection stays
  unchanged; its query context reflects its own effective config.
- [x] Outside assets require nonblank ASSQUACK_DATABASE_PATH or ASSQUACK_HOME;
  resource-only environment settings and the implicit cwd default are insufficient.
- [x] Read existing databases without metadata bootstrap or missing-file creation;
  preserve asset publication as the write path and reject mutating query statements.
- [x] Support joins across explicitly named local database sources and normal
  positional/named DuckDB parameter binding without loading source assets.
- [x] Make returned results usable after the query call and as asset inputs;
  establish deterministic connection ownership and cleanup, including errors.
- [x] Test same-database reads during materialization, lock/connection compatibility,
  nested/concurrent contexts, duplicate database paths, aliases, missing inputs,
  source immutability, result lifetime and cross-database output publication.
- [x] Run relevant offline tests, lint, types, formatting, doc links and diff checks;
  have an independent reviewer inspect and reproduce findings until resolved.

Table globbing is explicitly deferred. Its future requirement is full wildcard
patterns comparable to DuckDB file globbing, not starts-with matching. Snapshot
retention and Azure/lake source adapters are outside this implementation.
Do not claim a cross-database globally consistent snapshot or untrusted-SQL sandbox.

### Implementation details

The internal `_query/` package groups the query implementation by responsibility.
`asset.py` holds the existing asset/result query helpers; `general.py` owns source
selection, statement validation and read-only attachments. `context.py` manages
the executing asset's query config, and `result.py` owns `AssquackQueryResult`
and cleanup. `__init__.py` exposes the internal interfaces used elsewhere in the
package. The public `assquack.query` and result contracts remain unchanged.

General queries eagerly create a native DuckDB temporary result table, then detach
sources. The result owns its memory connection and private spill directory, with explicit close/context management and fallback
finalization. Asset consumption closes the wrapper after native JSON COPY.
There is no internal pandas conversion or mandatory Arrow dependency.

Capture retains the existing writer lock but closes its connection before calling
user code and consuming generators. It restores a writer connection for staging,
publication and failed-run metadata. A ContextVar covers that complete capture
interval without changing ordinary asset config selection. If reopening fails
after a capture failure, preserve the original error and leave the running record
for existing interrupted-run recovery.

Duplicate resolved source paths and case-insensitive duplicate aliases fail
clearly. Query settings come from the first explicit source, otherwise the
implicit selected config. Queries execute synchronously within the async entry
point; eager output can spill but is not a streaming query API. Unrelated open
DuckDB connections can still prevent read-only attachment.


### General query validation (29 October 2026)

- Full offline suite: 325 passed, 8 skipped for unavailable optional pandas/Arrow
  input dependencies. Temporary-directory access required sandbox escalation.
- Independent review approved the final code/docs with no known actionable
  in-scope bugs: 74 scoped tests passed, 8 optional skips, plus synthetic probes
  for real task cancellation, nested context restoration, empty/native results,
  source byte preservation and garbage-collected spill cleanup.
- Ruff source/test lint, formatting of all changed Python files, documentation
  links and `git diff --check` pass. The terminology scan retains only the
  historical cache/locking note and existing epic explanations.
- Pyright using the Poetry interpreter reports the same seven existing errors
  in unchanged `_api.py`, `_query.py` and `_templates.py` logic; none in new or
  changed logic. Broad source/test formatting retains six unchanged failures.
- An initial full run found a transient Windows file-in-use error in an existing
  cache test; independent scoped and final full runs passed without a retry
  mechanism or production change for that environmental failure.
- No live systems, customer credentials or `.env` files were accessed. No branch,
  commit or push was created. Table globbing remains deferred.


### Query package refactor validation (29 October 2026)

The query package split above is implemented without changing public behavior.
The old private `_query.py` and `_general_query.py` modules are removed, with no
compatibility shims. Existing tests do not patch those private modules, so no
patch targets needed updating. The phase remains In Progress for its other
unchecked work.

- Full offline suite: 325 passed, 8 optional pandas/Arrow dependency skips.
  Windows temporary-directory access required sandbox escalation.
- Ruff source/test lint, all changed Python file formatting, Markdown links and
  `git diff --check` pass. Broad formatting retains six unrelated failures.
- Pyright using the Poetry interpreter retains seven pre-existing diagnostics;
  the two former `_query.py` diagnostics now appear in unchanged `asset.py` logic.
- Terminology matches remain confined to the historical cache/locking note and
  its existing epic explanations. No live systems or customer data were used.
- Independent review approved with no known actionable in-scope defects:
  63 scoped tests passed, 8 optional dependency skips. Synthetic probes verified
  shared public/internal query and result identities, nested context restoration,
  native results and garbage-collected spill cleanup. AST comparison confirmed
  moved definitions are unchanged except the context accessor call.
