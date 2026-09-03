# Export Compatibility

Status: **Planned**
Last updated: 2026-09-03
Epic: 01 MVP
Phase: 04
Related docs: [Roadmap](../../roadmap.md), [Developer API](../../developer-api.md), [Exports](../../exports.md), [Storage Model](../../storage-model.md)

## Intent

Write compatibility artifacts from committed DuckDB tables without letting file
exports become the canonical asset state.

## Scope

Included:

- Export alias resolution for paths and file type aliases.
- Parquet current-state exports.
- `mad://` logical URI resolution to configured export base.
- `assquack_meta.exports` manifest rows for successful exports.
- ADLS/ABFSS export path support through DuckDB where available.

Excluded:

- Treating exports as cache truth.
- Durable raw JSON export as the default storage path.
- Broad fsspec fallback unless DuckDB-native export cannot meet a target.

## Architecture Notes

Model export writers as a narrow strategy boundary around the committed current
table. Export formats and URI schemes should not leak back into asset identity
or materialization state.

## Implementation Checklist

- [ ] Implement export target parsing and alias resolution.
- [ ] Implement Parquet export from committed current table.
- [ ] Record canonical `assquack_meta.exports` manifest rows.
- [ ] Add tests for file type aliases, pipe syntax, and resolved URIs.
- [ ] Add tests proving export failures do not corrupt successful table state.
- [ ] Update this status header before handoff.

## Validation

- `poetry run pytest`
- `poetry run pyright`
- `poetry run ruff check .`
- `poetry run ruff format --check .`
- export integration checks selected during implementation
- `git diff --check`

## Notes

Exports are an adapter surface around DuckDB tables. Keep the implementation
behind a small writer strategy so additional formats do not leak into the asset
decorator.
