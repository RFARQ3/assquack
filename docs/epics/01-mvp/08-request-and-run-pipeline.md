# Request snapshots and run stages

Status: **Complete**
Last updated: 2026-11-06
Epic: 01 MVP
Phase: 08
Related docs: [Architecture proposal](../../reviews/2026-11-06-architecture-and-run-flow.md), [Lifecycle](../../materialization-lifecycle.md)

## Implementation direction

Introduce `AssquackRequest` to preserve invocation arguments and options and
`AssquackRun` to record Resolve, Materialize, Stage, Publish and Export progress.
The coordinator lives in `_pipeline/__init__.py`. Each stage has its own module.
Fragment input handling stays with the writer in `_storage/fragment_writer.py`;
DuckDB-native inference lives in `_storage/schemas.py`.

Keep the existing MD5 asset/runtime run identifiers and public result fields.
Create a run object before cache lookup; cache hits do not insert a new run row.
New extraction attempts record each stage and distinguish publication success
from later export failure. Cleanup runs locally and retention runs once at exit.

## Validation

Follow-up: configuration is required on `AssquackRequest`. Request creation
selects call override, attached config or defaults and captures it once.
The pipeline accepts only the request; the run reads configuration from it.
Validation: 116 affected tests passed, covering configuration, request snapshots,
run stages, identity, modes, asset contracts/options and caching. Full Pyright,
Ruff lint/format, documentation links and whitespace checks passed.

- Request snapshot cases and producer mutation isolation.
- Stage start/outcome, rollback, export failure and cache retry behavior.
- Existing offline tests, Ruff, Pyright, documentation links and whitespace.
- Independent review of readability, scope and correctness.

## Metadata and request decisions

The fresh catalog adds five stage status columns, invocation `outcome`,
`failed_stage`, `error_type` and bounded `error_message`. Existing `status`
continues to describe publication for cache/retention. Publish updates success
and `current_publications(schema_name, table_name, run_id)` atomically with the
asset table. This fixes accepted DF-002: cache and manual exports use the actual
current publication, including empty tables and shared physical targets.

Resolve failures after database initialization now have an informative run row,
including invalid export destinations or credentials. No producer or export runs
on those failures. Invalid modes and unresolved table templates still fail before
database writes. Cache hits create no new run rows or public invocation IDs.

`AssquackAsset.__call__` captures the request synchronously and returns a coroutine.
Ordinary await usage is unchanged; callers changing arguments or config before
awaiting cannot rewrite the pending call. Built-in containers are copied with
aliases/cycles preserved. Custom objects and live runtime resources retain identity.
Producer arguments are separately copied so producer mutation preserves the original
request. Configuration captures public settings but retains destination providers.

No compatibility aliases, automatic migrations or schema-history storage are added.
The obsolete standalone file lock is removed; the real shared-connection lock and
overlapping-capture test remain. Current-publication and stage columns require a
fresh compatible metadata schema; existing user databases are not converted.

## Validation evidence — 6 November 2026

Independent reviewer approved source correctness and readability after the
implementation/review fixes. Full offline suite: **457 passed, zero skips**, on
Python 3.11.9 / DuckDB 1.5.6 with existing pandas, Arrow and numpy installations.
No live systems, credentials or customer data were accessed.

- `poetry run ruff check src tests`: passed.
- `poetry run ruff format --check src tests`: 82 files already formatted.
- Full configured Pyright source/tests check: zero errors and warnings.
- `git diff --check`: passed.
- Terminology scan: only existing downstream-infrastructure and historical notes.
- Documentation link check: all local Markdown targets and anchors pass after
  reconciling the proposal's historical paths and recording the final decisions.

The offline runner and independent review evidence are under
`.tmp/refactor-review/`. The configured type check explicitly selects the existing
installed environment so optional library imports resolve. Initial sandbox
permissions required automatically approved offline reruns; the results above
are from the stable implementation, not checks overlapping file moves.

The coordinator completed the final documentation and whitespace checks.
No changes have been committed.

## Main-pipeline lifecycle correction

The first implementation placed lifecycle handling in each stage helper. This
was the wrong interpretation of the requested DeltaBase structure. `run_pipeline`
now contains five numbered, explicit try/except sections with direct status
assignments and state saves. Helpers only perform the work and resource-local
cleanup. Publish's transaction and success save are visible in the main function.

The main function owns the `ExitStack` retaining export destinations and, on a
cache hit, the selected publication's lock. Failure handlers record the specific
stage and preserve the original exception, adding a stage/run note for the caller.
Failure saves are best-effort so they cannot obscure the original failure. The
request-only pipeline signature and immediate request capture remain unchanged.

The independent review must compare the actual main function beside historical
DeltaBase `process_data`, and inject errors directly at its stage helper calls to
prove the main pipeline records those failures itself. The correction passed independent validation: **467 tests passed, zero skips**
on the existing Python 3.11 / DuckDB 1.5.6 environment. Full configured Pyright
reported zero errors and warnings. Ruff lint, formatting (83 files), documentation
links and whitespace checks passed. The earlier results above describe the
previous structure.

The reviewer checked the five main-function blocks directly beside historical
DeltaBase `process_data`. Seven new lifecycle tests inject failures at the main
coordinator's helper calls, verify stage-specific error notes and original
exception/traceback preservation, cover cancellation, and prove a failed Publish
success-state save rolls back the table and metadata together. No lifecycle
start/success/failure recording remains in the stage work helpers.
