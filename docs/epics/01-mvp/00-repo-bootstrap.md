# Repo Bootstrap

Status: **Complete**
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

- [x] Add package directory and `__init__.py`.
- [x] Add `pyproject.toml` with runtime and development dependencies.
- [x] Add test runner configuration.
- [x] Add type-check configuration.
- [x] Add the first import-level tests for `from assquack import asset`.
- [x] Update this status header before handoff.

## Validation

- `poetry check`
- `poetry run pytest`
- `poetry run pyright`
- `poetry run ruff check .`
- `poetry run ruff format --check .`
- `git diff --check`

## Notes

Keep this phase intentionally small. Code is liability; bootstrap should create
only enough structure to support the next implementation phase cleanly.

Tooling choices:

- Poetry owns package metadata, dependency groups, lockfile generation, and
  project-local command execution.
- Pytest is the test runner.
- Pyright is the type checker because it matches the prior MAIT reference
  pattern and gives strong feedback for typed public contracts.
- Ruff owns linting and format checks so bootstrap does not split those concerns
  across multiple tools.
- The bootstrap decorator contract accepts both sync and async asset functions
  by default; actual async materialization behavior remains Phase 02 scope.
