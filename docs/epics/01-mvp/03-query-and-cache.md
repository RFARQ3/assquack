# Query And Cache

Status: **Planned**
Last updated: 2026-05-08
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

## Implementation Checklist

- [ ] Implement `AssquackResult` table reference behavior.
- [ ] Implement result and asset `.query(...)`.
- [ ] Implement `.exists()`.
- [ ] Implement `cache_first()` against metadata and current table presence.
- [ ] Add query parameter binding tests.
- [ ] Add cache-hit, cache-miss, stale-cache, and missing-table tests.
- [ ] Update this status header before handoff.

## Validation

- `pytest`
- type-check command selected during bootstrap
- `git diff --check`

Replace placeholder validation commands with concrete commands once Phase 00
chooses the project tooling.

## Notes

The query surface should stay small. Add convenience methods only when they
make common developer workflows clearer without expanding the storage model.
