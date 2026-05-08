# Assquack Agent Guide

## Project Identity

Assquack is a standalone DuckDB-native data asset library. The core package must
not depend on Prefect, Kubernetes, or any orchestrator. `.submodules/MAD.Prefect`
is reference material for migration research only.

README is for humans. This file gives agents the routing, workflow, and
guardrails needed to work in this repo.

## Repository Map

- `docs/README.md`: documentation index and knowledge map.
- `docs/*.md`: active architecture docs and examples.
- `docs/epics/[number]-[short-desc]/[phase-no]-[short-desc].md`: implementation
  epic phase tracking.
- `docs/assquack-mvp-plan.md`: archived monolithic planning note.
- `.agents/skills/assquack-documentation`: documentation workflow skill.
- `.agents/skills/assquack-coding`: coding workflow skill.
- `.agents/references/code-style.md`: implementation style and testing
  preferences.
- `.agents/references/preferences.md`: product and workflow preferences.
- `.submodules/MAD.Prefect`: historical reference only.

## Skill Routing

Use focused skills instead of expanding this file with task-specific detail.

- For documentation work, use
  `.agents/skills/assquack-documentation/SKILL.md`.
- For coding, scaffolding, tests, refactors, and implementation work, use
  `.agents/skills/assquack-coding/SKILL.md`.
- For style and product preferences, read the files in `.agents/references/`.

## Core Guardrails

- Keep Assquack standalone and framework-agnostic.
- Do not reintroduce `database_scope` as a public asset API argument.
- Use `VARIANT` for durable dynamic payload storage.
- Treat DuckDB tables and Assquack metadata as the source of truth.
- Treat exports as compatibility artifacts.
- Keep docs connected through relevant links, like a small knowledge graph.
- Add Mermaid diagrams when they clarify architecture, flows, state
  transitions, table relationships, or configuration precedence.
- Write implementation code like prose: group related ideas with whitespace,
  use clear names, and add narrative comments or docstrings only when they
  explain intent, flow, or a non-obvious contract.
- Prefer strongly typed public contracts and interfaces so developer tooling can
  provide useful autocomplete and type feedback.
- Treat code as liability: prefer the smallest clear implementation, and add
  abstraction only when it improves future maintainability.

## Workflow Expectations

- For non-trivial feature, API, storage, schema, export, or configuration
  changes, establish the developer-facing direction before implementation.
- Confirm direction with the user when the public contract or architecture is
  ambiguous or materially changing.
- Do not block on confirmation for small, obvious fixes or follow-through from
  an already approved direction.
- Add or update tests for behavior changes.
- Update docs/examples for public behavior or architecture changes.
- For relevant coding tasks, check or create the touched epic phase docs before
  implementation, keep regular docs developer-first, keep epic docs
  implementor-facing, and update epic status headers before handing work back.
- Set a touched phase to `In Progress` when implementation begins; keep it
  there until implementation, docs, tests, and checks are done; use `Blocked`
  when a dependency or decision prevents progress.
- Run relevant tests, lint, type checks, and `git diff --check` when available.
- Do not commit unless the user asks. If committing, use Conventional Commits.

## Validation

For documentation changes, run the checks in the documentation skill. For code
changes, run the checks in the coding skill and any project tooling introduced
by the package scaffold.

Always report checks run and any checks that could not run.
