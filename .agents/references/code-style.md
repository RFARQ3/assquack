# Assquack Code Style

Use this reference for implementation work once the Python package exists.

## Python

- Target Python 3.11+ unless the project metadata says otherwise.
- Prefer strongly typed public contracts so editor autocomplete and IntelliSense
  are useful. Use precise types for decorators, protocols/interfaces, result
  objects, configuration models, and storage interfaces.
- Public contracts and interfaces should have explicit return types. Internal
  helpers may omit return annotations when the inferred return type is clear,
  stable, and improves readability.
- Keep framework dependencies out of core code. Assquack must not import
  Prefect, Kubernetes clients, or orchestration-specific packages in the core
  package.
- Use Pydantic for configuration and durable metadata models when structured
  validation matters.
- Keep functions small and single-purpose, but avoid premature abstraction.
- Prefer explicit exceptions with actionable messages over broad catch-and-log
  behavior.

## Design Philosophy

- Treat code as liability. A strong implementation usually succeeds with the
  smallest amount of code that clearly satisfies the contract.
- Add abstraction only when it reduces real complexity, protects a public
  contract, or makes future maintenance easier.
- Think in design patterns before writing code. Prefer established shapes such
  as strategy, adapter, repository, unit of work, or builder when they clarify
  extension points and keep the next maintainer from guessing.
- Do not use patterns as decoration. If a plain function or small dataclass is
  easier to understand and change, use that.
- Keep extension points explicit at boundaries: asset decorators, config
  loading, storage transactions, schema inference policy, export writers, and
  optional adapters.

## Code Like Prose

- Use spacing to group related ideas the way prose uses paragraphs. Keep lines
  that serve one thought together, then add a blank line before moving to
  validation, planning, execution, persistence, or handoff.
- Prefer clear names over comments. Name functions by outcome, booleans
  positively, include units where they matter, and use `row`, `record`, `list`,
  or `map` cues when the data shape is important.
- Add docstrings for multi-step helpers that orchestrate a flow. Summarize the
  path through the function, not every implementation detail.
- Use paragraph-style comments before non-obvious blocks to explain why the
  next step exists or what contract it protects.
- Do not add comments that merely restate syntax, such as "loop over rows" or
  "set the value". If the code is obvious, let it stand.
- Comment at boundaries: schema drift decisions, durable metadata writes,
  table swaps, compatibility exports, performance trade-offs, and surprising
  guard clauses.
- Prefer guard clauses and named helpers over deeply nested control flow.

For multi-phase code, this is the target shape:

```python
def materialize_asset(run: AssetRun) -> MaterializationResult:
    """Stage yielded records, infer a stable shape, then publish atomically."""

    # Reject invalid runs before opening a transaction so failures stay cheap.
    validated_run = validate_run(run)
    if validated_run.is_empty:
        return MaterializationResult.empty(validated_run)

    # Preserve the raw dynamic payload first so schema inference can revisit it.
    staging_table = stage_variant_payloads(validated_run)

    # Promote only fields that are stable enough for typed analytical columns.
    inferred_schema = infer_schema(staging_table)
    shaped_table = apply_schema(staging_table, inferred_schema)

    return publish_table_swap(shaped_table)
```

## DuckDB And SQL

- Treat DuckDB as the canonical storage engine, not just an export/query helper.
- Use transactions around materialization commits and metadata updates where
  DuckDB supports the boundary.
- Keep generated SQL isolated in helpers with tests for quoting, identifiers,
  and parameters.
- Use DuckDB parameter binding for values. Do not interpolate user values into
  SQL strings.
- Use `VARIANT` for durable dynamic payloads and explain JSON as ingestion or
  inference tooling where relevant.

## Tests

- Add focused unit tests for new behavior.
- Prefer temporary `.duckdb` files for storage tests.
- Cover schema drift, missing fields, and failed casts for inference changes.
- Cover export path resolution and manifest records for export changes.
- If a requested check cannot run because project tooling is not scaffolded yet,
  say so explicitly in the handoff.

## Documentation

- Document public behavior changes in `docs/`.
- Add Mermaid diagrams when they clarify flow, table relationships, or state
  transitions.
- Keep examples short and move extended examples to
  `docs/developer-examples.md`.

## Commits

- Do not commit unless the user asks.
- When committing, use Conventional Commits:
  `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`.
- Commit only intentional, reviewed changes. Do not include unrelated dirty
  work.
