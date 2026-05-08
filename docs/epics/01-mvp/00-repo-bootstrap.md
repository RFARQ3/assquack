# Repo Bootstrap

Status: **Planned**
Last updated: 2026-05-08
Epic: 01 MVP
Phase: 00
Related docs: [Roadmap](../../roadmap.md), [Developer API](../../developer-api.md), [Configuration](../../configuration.md)

## Intent

Prepare the repository to host the first Assquack package implementation without
pulling in orchestration dependencies or overbuilding abstractions before the
core contracts exist.

## Scope

Included:

- Python package skeleton for `assquack`.
- Project metadata and dependency declarations.
- Test, lint, and type-check tooling.
- Public import surface for the MVP API shape.
- Minimal documentation updates needed to keep bootstrap decisions traceable.

Excluded:

- Materialization behavior.
- DuckDB metadata table bootstrap.
- Export writing.
- Optional adapters.

Those belong to later phases once the package and tooling are in place.

## Architecture Notes

Bootstrap should establish package boundaries without committing to broad
framework abstractions. Keep the first public surface small and strongly typed,
then let later phases introduce patterns where they protect extension points.

## Implementation Checklist

- [ ] Add package directory and `__init__.py`.
- [ ] Add `pyproject.toml` with runtime and development dependencies.
- [ ] Add test runner configuration.
- [ ] Add type-check configuration.
- [ ] Add the first import-level tests for `from assquack import asset`.
- [ ] Update this status header before handoff.

## Validation

- `pytest`
- type-check command selected during bootstrap
- lint/format check selected during bootstrap
- `git diff --check`

Replace placeholder validation commands with concrete commands once this phase
chooses the project tooling.

## Notes

Keep this phase intentionally small. Code is liability; bootstrap should create
only enough structure to support the next implementation phase cleanly.
