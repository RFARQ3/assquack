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

## Planned: asset options and attached config

Scope added 29 October 2026; implementation has not started. The agreed
[public API goal](../../developer-api.md#planned-asset-config-and-option-copies)
adds decorator `config=` and immutable `with_options()` customisation. The phase
remains In Progress; this subsection is planned work, not completed behaviour.

- [ ] Accept an optional default `AssquackConfig` in the asset decorator.
- [ ] Add typed `with_options()` parameters matching decorator settings, including
  config; retain omitted options and distinguish explicit nullable resets.
- [ ] Preserve the wrapped function, bound arguments and original templates when
  copying; apply changed templates correctly to already-bound arguments.
- [ ] Preserve attached config through `with_arguments()`, `cache_first()` and
  other option copies without modifying the original asset or shared config.
- [ ] Resolve config consistently for execution, asset queries and existence
  checks: explicit call config, then attached config, then normal defaults.
- [ ] Verify independent config objects direct assets to independent database
  paths; no environment-label field or new config-provider abstraction.
- [ ] Test option resets, cache policy consistency, chaining in either order,
  template resolution and absence of execution during configuration.
- [ ] Update Namely to attach its shared config at asset definition rather than
  repeat it at every call, retaining explicit overrides only where intended.
- [ ] Update runnable examples after implementation; do not add `with_config()`.

Use offline synthetic data for validation. The present documentation task does
not authorise implementation or change current runtime behaviour.
