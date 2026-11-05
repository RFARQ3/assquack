# Assquack design review remit

Status: ready to commission
Prepared: 4 November 2026

## Assignment

Review the repository for coherent design and a simple downstream developer
experience. Ask whether the library makes sense as a whole, not just whether
individual functions pass tests. Produce findings and proposed corrections;
do not implement changes as part of this review.

The goal is a small, standalone DuckDB-native asset library that preserves the
useful MAD.Prefect behaviours requested by the owner. Reduce memory use, improve
writing performance, and keep the implementation understandable. Append and merge
are planned extensions. Review whether today's design supports adding them cleanly,
and flag choices that would obstruct them. Implementing those modes is outside
this review's scope.

## Authority and boundaries

- Treat the owner's explicit decisions as requirements. Repository documents
  record decisions but are not automatically proof that the owner approved them.
  Flag conflicts between instructions, documents, tests and code; do not silently
  choose a new contract.
- This is an initial implementation. Do not propose migrations, compatibility
  shims or support for hypothetical existing consumers. Namely is the current
  trial integration, not a reason to freeze an incorrect API.
- Review the intended single-process use case. Do not redesign the MVP for
  multiple processes or pods, nonblocking writes or speculative deployment needs.
- Read source, tests, docs and history. Use offline synthetic probes only when
  they resolve a specific uncertainty. Do not read environment files, credentials,
  customer databases or datasets, or run live APIs or flows.
- Do not edit implementation, delete files, change dependencies, commit, push or
  create branches. Do not start a new benchmark without a concrete unanswered
  performance question. An approval of this remit authorizes review, not fixes.
- Describe observable changes and evidence, not motives or assumptions about
  previous authors. Passing tests is evidence of tested behaviour, not design
  approval; tests may encode the wrong requirement.

## Decisions to preserve

Verify these against the actual implementation rather than assuming they work:

- The table-first asset decorator retains its optional `config=` argument.
- Calling an asset invokes materialization with its existing caching policy.
  `asset.query()` invokes that same path and then queries the returned result.
  `result.query()` and general `assquack.query()` do not invoke source extraction.
  Do not invent a new materialize/chaining API.
- Query results are native `DuckDBPyRelation` objects that can participate in
  `duckdb.query()` through Python variables on the actual default connection.
- Configs identify database attachments. Multiple database destinations share
  that connection. With one user database attached, short table references work;
  with multiple user databases, physical references require
  `database.schema.table`. Python relations and query-local CTEs are exempt.
- Native fragment capture writes directly from the relation to JSON, without an
  intermediate view, registration, Parquet file or conversion transport.
- Lazy relations are written once, with empty COPY output removed before fragment
  registration. Cheap emptiness checks remain appropriate for materialized inputs.
  Generators are consumed incrementally and their yielded values are dispatched.
- Oversized JSON reading retains the initial attempt, bounded largest-record byte
  measurement after the relevant failure, and one adjusted retry. It must not
  rerun extraction or become unconditional whole-dataset scanning.
- The baseline uses synchronous blocking writes and an in-memory connection lock.
  Distinguish this from file locking. Identify any remaining file-lock use
  precisely rather than asserting that all locks are equivalent or all removed.
- Do not reintroduce the removed SELECT-only prevalidation helper or equivalent
  restrictions under a different name.
- DuckDB tables and metadata are canonical; exports are compatibility outputs.
  Keep filesystem/authentication behaviour, export formats and schema inference
  intact unless a specific finding is reviewed and a change is authorized.

## Review method

### 1. Inventory the public developer experience

List the public operations on assets, results, configs and the general query API.
For each, record its inputs, return type, side effects and owning implementation.
Check naming, argument conventions, defaults, sync/async behaviour and consistency
between comparable operations. Follow real examples, not only method signatures.

Walk through first use, repeated use with caching, bound asset arguments, nested
dependencies, querying an existing result, Python relation chaining, automatic
exports, manual exports and a deliberately attached second database. Include the
Namely snapshot pattern without running its customer extraction code.

### 2. Trace implementation responsibilities

Follow each operation from the public entry point through config resolution,
execution, storage, query binding and exports as applicable. Identify duplicated
or inconsistent logic, special cases placed in the wrong layer, misleading names,
and unnecessary forwarding or abstractions. A short public method delegating to
another module is not itself a defect; assess whether the boundary has a purpose.

### 3. Compare requirements and reference behaviour

Read the relevant MAD.Prefect source, including methods it calls, for each claimed
equivalence. Distinguish deliberate approved differences from lost functionality.
Do not copy its orchestrator or filesystem dependencies into Assquack. Check the
current tests and docs against requirements rather than using them to justify
every current behaviour.

### 4. Challenge complexity with concrete evidence

For a validation, fallback, lock, helper or special case, identify the actual
requirement it serves. Ask whether DuckDB or an existing layer already does the
work, and what would really happen if it were removed. Do not assume all
validation is bad: explain a concrete data-integrity or required behaviour benefit
when one exists. Do not recommend guards solely because someone might misuse an
API. Prefer removing duplication over adding an abstraction to conceal it.

### 5. Assess consequences and scope

For every proposed correction, name the smallest affected surface and any approved
decision it would change. Trace downstream effects before recommending it.
Explicitly distinguish an observed bug, an API inconsistency, an unnecessary
implementation choice, a documentation mismatch and an unresolved product choice.

## Calibration examples

- `result.export_parquet()` exists while the shared writer supports Parquet, CSV
  and JSON. Explain the inconsistent manual export surface, its history and its
  consequences. Compare a general export method with retaining export setup on
  assets; do not automatically add a method for every format. No export redesign
  is approved by this remit.
- A connection lock named `DATABASE_LOCK` obscured its purpose. Verify the current
  `CONNECTION_LOCK` name and distinguish remaining file-lock code. Do not report a
  resolved naming issue as a new finding.
- An asset query that bypasses invocation loses MAD.Prefect's materialize/cache
  behaviour. Verify the corrected path and inspect results independently of
  re-extraction when testing failures; do not reintroduce the old behaviour.

These examples calibrate the review. They are not a request to invent more
findings, reopen settled decisions or limit inspection to those files.

## Deliverable

Start with a short verdict and the most consequential findings. For each finding:

1. State the concrete inconsistency or failure and its developer-visible effect.
2. Give a minimal example and exact source path/line references, with supporting
   requirements or reference code where relevant.
3. Explain why it matters in the intended MVP use case. Separate verified facts
   from inference and state uncertainty honestly.
4. Propose the smallest correction, the affected files/APIs and any decision that
   needs the owner's agreement. Do not implement it.
5. State focused acceptance checks that prove the proposed outcome without adding
   speculative testing or defensive code.

Assign each design flaw a stable ID, such as `DF-001`, so the findings and file
table can refer to the same issue without duplicating its full explanation.

Rank findings by impact: incorrect behaviour or broken requirements first, then
API inconsistencies and unnecessary complexity, then naming/documentation issues.
Keep optional preferences separate. Avoid a numerical style score or a long list
of cosmetic complaints. No findings is acceptable when supported by the review.

### Complete repository file table

After the design-flaw list, include a table with **one row for every repository
file**, sorted by repository-relative path. Do not limit it to changed files,
Python files or files with findings. Include source, tests, documentation,
configuration, scripts, committed benchmark evidence and other repository files.

Build the inventory from Git-tracked files plus untracked, non-ignored files in
the current checkout. Exclude Git internals, ignored local/generated files and
deleted working-tree paths. Do not open secrets or live data to complete the
inventory; record any access limitation in the review coverage notes. Identify
submodule roots separately and inventory their files within their own repository
boundary when they are included in the review.

Use exactly these columns:

| File | Design bugs |
| --- | --- |
| `path/to/file.py` | DF-001 — concise finding title; DF-004 — concise finding title |
| `path/to/file_without_findings.py` | |

The example paths above are illustrative; replace them with the actual inventory.
The **Design bugs** cell must list every raised design flaw relevant to that file.
A flaw affecting several files must appear against each affected file, using the
same ID. Every ID must resolve to an explained finding in the design-flaw list.
For a file with no identified design bugs, leave that cell **empty**: do not write
"none", "clean" or a dash. Do not invent a finding to populate the table.

Reconcile the completed table against the file inventory: report the total file
count and verify there are no missing paths or duplicate rows. A blank cell means
no flaw was identified, not that an unreviewed file has been certified correct;
list any unreviewed or non-inspectable files explicitly in the coverage notes.

Finish with coverage: the reviewed commit and working-tree state, modules and
public paths inspected, reference material used, probes/checks run, and anything
not reviewed. Explicitly state that no implementation changes were made. Do not
claim repository-wide coverage from a narrow diff review.

## Related Docs

- [Documentation index](../README.md): architecture and implementation map.
- [Developer API](../developer-api.md): current public contracts to verify.
- [Configuration](../configuration.md): configs, attachments and engine settings.
- [Materialization lifecycle](../materialization-lifecycle.md): capture and publication.
- [Exports](../exports.md): shared export behaviour and manual entry points.
- [MAD.Prefect reference](../mad-prefect-reference.md): historical reference context.
- [Repository preferences](../../.agents/references/preferences.md): product boundaries.
