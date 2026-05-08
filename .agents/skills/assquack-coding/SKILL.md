---
name: assquack-coding
description: Implement Assquack code changes. Use when Codex is asked to scaffold package code, add or change Python behavior, implement DuckDB storage/materialization/schema/export/config functionality, add tests, fix bugs, refactor code, prepare release-quality implementation work, or update developer-facing docs as part of a code change.
---

# Assquack Coding

## Overview

Use this skill for implementation work in Assquack. Keep the core package
standalone and DuckDB-native.

Before coding, read:

- `.agents/references/preferences.md`
- `.agents/references/code-style.md`
- relevant docs in `docs/`, especially `developer-api.md`,
  `materialization-lifecycle.md`, `schema-inference.md`, `storage-model.md`,
  `exports.md`, and `configuration.md`.

## Workflow

1. Inspect the current repo state and relevant files.
2. Identify whether the request changes public API, storage behavior, schema
   inference, exports, configuration, or migration posture.
3. For non-trivial changes in those areas, document or summarize the
   developer-facing direction first. Confirm with the user when the direction is
   ambiguous or materially changes the public contract.
4. Implement the narrowest code change that satisfies the request.
5. Add or update tests for behavior changes.
6. Update docs when public behavior, architecture, configuration, or examples
   change. Use the `assquack-documentation` skill for substantial docs work.
7. Run relevant tests, lint, type checks, and `git diff --check` when available.
8. Review the diff for unrelated edits, regressions, and terminology drift.
9. If the user asks for a commit, use a Conventional Commit message.

Do not pause for confirmation on small, obvious bug fixes or mechanical
follow-through from a direction the user has already approved.

## Implementation Rules

- Do not import Prefect, Kubernetes clients, or orchestration-specific packages
  in core Assquack code.
- Keep database placement in configuration, not in asset decorators.
- Treat DuckDB tables and Assquack metadata as the source of truth.
- Use exports for compatibility, not canonical storage.
- Use `VARIANT` for durable dynamic payloads.
- Prefer explicit models and interfaces for assets, results, runs, manifests,
  and configuration.
- Treat code as liability. Prefer the smallest clear implementation, but use
  design patterns when they make extension points or future maintenance easier.
- Make public contracts strongly typed for developer experience. Require
  explicit return types on public APIs and interfaces, but allow internal helper
  return inference when it is clear and stable.
- Shape code into readable paragraphs. Use blank lines to separate logical
  phases, and add narrative comments or docstrings for multi-step flow when the
  intent is not obvious from names alone.
- Use parameter binding for values in SQL.
- Keep generated identifiers and SQL quoting centralized and tested.

## Testing Expectations

- For storage changes, test with temporary `.duckdb` files.
- For schema inference changes, test missing fields, type drift, sparse objects,
  array shape drift, and failed casts.
- For export changes, test alias resolution and manifest records.
- For configuration changes, test defaults, environment override behavior, and
  validation errors.
- If project test tooling is not scaffolded yet, say exactly which checks could
  not run and why.

## Done Criteria

A coding task is done when:

- code implements the requested behavior;
- relevant tests are added or updated;
- relevant docs/examples are updated;
- available checks pass, or unavoidable gaps are reported;
- the final response lists changed areas and verification;
- no unrelated user changes are reverted.
