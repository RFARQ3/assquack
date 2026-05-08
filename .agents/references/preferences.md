# Assquack Preferences

Use this reference for product and workflow preferences that should survive
across agent sessions.

## Product Direction

- Assquack is standalone and framework-agnostic.
- MAD.Prefect is reference material only. Optional adapters may depend on
  Assquack; Assquack must not depend on them.
- Keep the developer API simple and path-first:
  `@asset("this/is/an/export.parquet")`.
- Keep `name`, `table`, and `mode` optional.
- Do not reintroduce `database_scope` or database placement as public asset
  decorator arguments.
- Use DuckDB 1.5+ as the likely baseline because `VARIANT` is central to the
  design.
- Treat exports as compatibility artifacts, not canonical storage.

## Documentation Direction

- Keep `docs/README.md` as the documentation index.
- Treat docs like a knowledge graph: each active architecture page should link
  to adjacent pages through `## Related Docs`.
- Preserve `docs/assquack-mvp-plan.md` as the archived monolithic plan.
- Use Mermaid diagrams when they clarify architecture, flow, table
  relationships, state transitions, or configuration precedence.

## Agent Workflow Preferences

- For non-trivial feature, API, storage, or architecture changes, establish the
  developer-facing direction before implementation. Use docs or a concise plan.
- Confirm direction with the user when the change affects public API,
  architecture, data model, or migration posture and the desired direction is
  not already explicit.
- Do not block for confirmation on small, obvious bug fixes or mechanical
  follow-through from an already approved direction.
- Implement tests with behavior changes. If tooling does not exist yet, note
  the gap and add tests once the package scaffold exists.
- Run relevant tests, lint, type checks, and `git diff --check` before final
  handoff when available.
- Prepare Conventional Commit wording when useful, but commit only when the user
  asks.
- Treat the working tree and staging area as user-controlled. Preserve existing
  hunks, do not unstage or revert unrelated changes, and ask before touching a
  file when preserving another author's edits is not feasible.
- Keep handoffs reviewable: describe what changed, why it changed, checks run,
  blockers, and any follow-up decisions.
