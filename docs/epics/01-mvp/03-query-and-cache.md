# Query And Cache

Status: **In Progress**
Last updated: 2026-10-28
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

## Implementation Checklist

- [ ] Implement `AssquackResult` table reference behavior.
- [ ] Implement result and asset `.query(...)`.
- [ ] Implement `.exists()`.
- [x] Implement `cache_first()` against metadata and current table presence.
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
