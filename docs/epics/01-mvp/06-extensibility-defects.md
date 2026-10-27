# MVP Extensibility Defects

Status: **In Progress**
Last updated: 2026-10-26
Epic: 01 MVP
Phase: 06
Related docs: [Scope journal](../../journal/2026-09-08-mvp-scope.md), [Roadmap](../../roadmap.md), [Export compatibility](04-export-compatibility.md)

## Purpose and authority

Track the extension barriers found in the 23 October source review so Rowan can
work through them individually and commission implementation agents when ready.
This document records findings and proposed acceptance criteria; it does not
authorize coding, launch agents, or settle unresolved public API decisions.
The coordinating chat performs fact-finding and oversight. Implementation needs
Rowan's explicit instruction for the selected work.

The review covered the current working tree, including uncommitted Azure work.
Evidence below is source inspection, not a fresh test run or live verification.
Recheck the relevant code before assigning an item because other chats may change it.

## Extensibility goal

The MVP should provide a useful standalone base for future features. Adding an
implementation at an agreed boundary should not require rewriting unrelated
execution code. An interface alone is insufficient if callers cannot select an
implementation or it cannot obtain the context it needs.

Proposed acceptance rule: an additional implementation can be supplied and
selected through a documented boundary, without monkeypatching internals. Tests
demonstrate that normal execution uses it and preserves cleanup, failure and
metadata contracts. Registry behavior must define duplicate names, unknown names,
defaults and isolation between callers; the exact public registration API remains
to be agreed before implementation.

This does not add S3, schema continuation, observations, append or merge to the
shipped MVP feature set. Local DuckDB, local NDJSON fragments, one writer,
current-run inference and replace-only publication remain deliberate choices.
Do not introduce a plugin system for every helper or promise that every future
feature will require zero core changes.

## Work register

### Agreed export design — 26 October 2026

The [scope decision](../../journal/2026-09-08-mvp-scope.md#agreed-filesystem-destination-pattern-26-october-2026)
now establishes `FileSystemDestination(base_uri, auth)` implementing the broader
`ExportDestination` contract. One internal DuckDB filesystem adapter composes
registered local/Azure preparation strategies with compatible authentication;
the separate DuckDB writer performs shared COPY. Preparation classes remain
small and retain explicit ownership rather than duplicating DuckDB internals.

Writer and filesystem adapter are replaceable internally, but DuckDB is the only
MVP implementation and neither is publicly configurable. Future public selection
is deliberately left open. For EXT-03/04, demonstrate internal substitution via
the registry/construction boundary without monkeypatching or changing the shared
export flow; do not require a public alternative-writer/adapter setting to close
that agreed slice. Formats are selected from asset declarations, independently
of destination configuration. Database destinations remain a future extension
of the parent contract, not a filesystem-adapter implementation.

This records direction only: the findings remain open pending implementation
and verification. REL-01 publication safety remains separate required work;
direct Azure COPY is not made failure-preserving by the preparation refactor.
See the [source research](../../research/2026-10-26-duckdb-filesystem-preparation.md)
for verified setup differences and the unresolved ADLS parent behavior.

Pipe-format implementation is assigned to the visible implementation chat, with
independent review in the visible review chat. This slice covers built-in formats,
target expansion, truthful manifests and all-target cache qualification. Broader
writer registration and destination/authentication registries remain open.
Priorities below are review recommendations, not a revised approved release scope.

| ID | Finding | Disposition | Status |
|---|---|---|---|
| EXT-01 | Azure authentication selection is hardcoded | Proposed pre-release repair | Open |
| EXT-02 | Export credential-provider injection boundary | Internal entry point agreed; public injection and new providers deferred | Open |
| EXT-03 | Parquet writing owns destination routing | Proposed pre-release repair | Open |
| EXT-04 | Export format selection and metadata are hardcoded | Proposed pre-release repair | Open |
| EXT-05 | Schema strategy selection and context are insufficient | Internal registry and argument-free resolution implemented | Complete |
| EXT-06 | Materialization mode does not reach execution or metadata | Internal registry and mode propagation implemented | Complete |
| EXT-07 | Input support gaps and normalization design | Missing features separated from proposed refactor; writing path undecided | Open |
| EXT-08 | Observation extension point is undefined | Design decision required | Open |
| EXT-09 | Catalog evolution has no versioned migration mechanism | Earlier unreleased catalogs need no compatibility; future upgrades deferred | Deferred |
| REL-01 | Export write failure preservation is incomplete | Existing export acceptance work | Open |
| REL-02 | Public export settings/types exceed implemented behavior | Contract reconciliation required | Open |
| REL-03 | Release validation evidence is incomplete | Existing release acceptance work | Open |

## EXT-01: Azure authentication strategy registry

**Evidence:** [azure.py](../../../src/assquack/_exports/azure.py) defines
`AzureAuthStrategy`, `CredentialChainAuth` and `SasAuth`, but
`select_azure_auth()` chooses concrete classes through conditionals. The context
manager uses `isinstance(strategy, SasAuth)` for error handling.

**Consequence:** adding an authentication strategy requires editing selection
logic and potentially shared error handling. Strategy classes exist; a registry
does not.

**Proposed outcome and acceptance:**

- Select registered authentication strategies through a defined boundary.
- Preserve automatic selection and explicit override precedence for built-ins.
- Give strategies a common lifecycle and credential-safe failure contract.
- Exercise a synthetic extra strategy through normal export execution without
  editing the selector or branching on its concrete class.
- Verify temporary secret cleanup and diagnostic redaction on failures.

## EXT-02: Export credential provider injection

This item concerns obtaining credentials for export destinations. It does not
cover authentication for source extraction or other application services.

**Evidence:** [azure.py](../../../src/assquack/_exports/azure.py) accepts a provider
in `azure_export_auth()`, defaulting to `EnvironmentSasProvider`.
[parquet.py](../../../src/assquack/_exports/parquet.py) never supplies one.
[azure_credentials.py](../../../src/assquack/_exports/azure_credentials.py)
contains the provider protocol, but normal asset/result exports expose no route
to inject a provider.

**Decision, 26 October (Rowan):** retain a non-public entry point for direct
provider-instance injection, defaulting to environment-based sourcing. Do not
create a credential-provider registry or expose provider configuration yet.
Implementations fetching Prefect blocks, vaults or other new sources are deferred.
Future downstream providers can implement the contract themselves without being
registered or included in the core package. Authentication and destination
strategy registries remain separate and unchanged by this decision.

**Revised outcome and acceptance:**

- Establish the internal injection boundary without a new public provider API;
  preserve the environment default. Absence of public injection is intentional.
- When implementing the boundary, verify internal substitution with a synthetic
  provider through automatic and explicit export execution, without monkeypatching
  or real credentials. Do not implement an external integration merely to test it.
- The intended contract is asynchronous `load()` followed by synchronous
  `get(name)`. Provider-owned loading needs no separate fetch callback. Resolve
  synchronous execution integration before claiming support; this decision does
  not claim that the current code already implements that lifecycle.
- Retrieve only requested values; keep secrets out of public serialized settings,
  metadata and diagnostics. Local exports must not fetch Azure credentials, and
  native credential-chain discovery stays with DuckDB.
- Keep external credential integrations outside core dependencies. Do not infer
  that this entry point alone settles multiple-destination credential selection.

This item remains open for the internal boundary work, not for delivery of a
public provider registry or additional credential-source implementation.

## EXT-03: Destination strategies

**Evidence:** [parquet.py](../../../src/assquack/_exports/parquet.py) directly
branches between Azure and local paths and rejects other URI schemes.

**Consequence:** adding S3 requires modifying format-writing code. Moving Azure
files into a folder alone would not remove this coupling.

**Proposed outcome and acceptance:**

- Resolve a destination strategy by URI scheme, separate from format writing.
- Define ownership of URI validation, connection preparation, cleanup and output
  publication. Coordinate publication guarantees with REL-01.
- Demonstrate an additional synthetic destination using the shared Parquet writer
  without changing it or the materialization pipeline.
- Preserve local paths, file URIs, Azure URIs and alias behavior.

## EXT-04: Export writers and manifest data

**Evidence:** [pipeline.py](../../../src/assquack/_materialization/pipeline.py)
and [result API](../../../src/assquack/_result.py) instantiate
`ParquetExportWriter` directly. [targets.py](../../../src/assquack/_exports/targets.py)
rejects other formats and appends Parquet suffixes.
[metadata.py](../../../src/assquack/_storage/metadata.py) records fixed
`parquet`, `default` and empty options values.

**Consequence:** implementing the existing `ExportWriter` protocol alone does
not make another format usable or accurately recorded.

**Proposed outcome and acceptance:**

- Resolve writers by format and carry the actual format/options through target
  resolution, execution and successful manifest recording.
- Share selection behavior between automatic and explicit exports.
- Support the pipe-separated formats required by Rowan on 23 October: expand
  declarations into targets before writer/destination selection, using the same
  committed table for all outputs. Cover path and type-only forms, manifest
  records for each successful output and failure partway through the output list.
- Demonstrate a synthetic second writer without changing either execution path;
  assert accurate manifest data and no success manifest after writer failure.
- Preserve the existing Parquet convenience API. Rowan confirmed on 23 October
  that Parquet, CSV and JSON must all ship in MVP. Prove extension separately with
  a synthetic writer; implementing only the three built-ins does not prove that
  another writer can be registered.

### Implemented pipe-format slice — 23 October 2026

The commissioned implementation now selects built-in Parquet, CSV and NDJSON
writers after expanding path/type-only aliases. It records each successful
output's actual format, role and fixed options; cache qualification requires all
requested resolved targets on the same run. Malformed declarations fail before
extraction, and a failure partway through exports keeps only completed manifests.
Automatic and explicit Parquet exports share writer selection.

This implements the built-in-format portion of EXT-04 and REL-02. Registration of
an additional writer remains unimplemented, so EXT-04 stays open. Custom export
options are now rejected; snapshot settings and the remaining REL-02 contract
work are unchanged. REL-01 staged publication and all destination/authentication
registry work remain open. See the [export phase](04-export-compatibility.md#pipe-formats-implementation-23-october-2026)
for verification and the separate review outcome.

## EXT-05: Schema strategy selection and context

**Approved direction, 26 October 2026 — Complete:** this is framework-owned
machinery, not a public registration or custom-strategy injection API. The
general public-extension acceptance rule above does not apply to EXT-05.

- An immutable internal registry selects only `current_run`.
- The shared contract is `resolve(self)`, with no schema or context arguments.
- Each strategy instance binds its own run connection and fragment dependency
  internally. `CurrentRunSchemaStrategy` obtains completed fragments and performs
  native inference itself. No global mutable run state is permitted.
- The pipeline selects the strategy and calls `resolve()` without prefetching a
  schema or branching on strategy-specific behavior.
- Remove unused historical schema persistence and obsolete observation helpers.
  New catalogs omit those tables; existing tables and records remain untouched.
- Preserve current-run drift, missing fields, heterogeneous values, empty runs,
  cache behavior and atomic replacement/failure behavior with offline tests.

Continuation, history access, public injection and public registration are
deferred; no continuation config value is introduced. Future framework strategies
own their dependencies without changing the shared `resolve()` contract.

**Validation:** the full offline suite passed (173 tests), followed by all five
schema-inference tests after adding explicit failed-cast coverage. Coverage
includes independent run resources, native nested inference, drift, empty runs,
cache hits, schema failure/retry, atomic rollback and fresh metadata catalogs.
Ruff lint, changed-file formatting, documentation links and `git diff --check`
pass. Pyright with the Poetry interpreter reports 13 errors in untouched code
(`_api.py`, `normalize.py`, `_query.py`, `_templates.py`); none in this change.
Sandbox restrictions prevented pytest temporary-directory access and dependency
resolution for Pyright; the successful checks ran outside the sandbox using only
offline synthetic data. No live services or credentials were accessed.

The phase remains In Progress because other register items remain open.

## EXT-06: Materialization mode propagation

**Status: Complete — 26 October 2026.** Approved scope: carry the declared
mode through the request, select publication through a framework-owned registry,
and persist that same mode. Only replace ships. No public registration/injection,
mode-specific options, append/merge algorithms or compatibility layer is added.

**Original finding:** [asset API](../../../src/assquack/_api.py) stores `mode`, but
[MaterializationRequest](../../../src/assquack/_materialization/models.py) omits
it. [pipeline.py](../../../src/assquack/_materialization/pipeline.py) defaults
directly to `ReplaceCommitStrategy`; [AssetRecord](../../../src/assquack/_storage/models.py)
independently defaults its stored mode to `replace`.

**Consequence:** adding another commit strategy is insufficient to select it
from the asset declaration or record its mode correctly.

**Implemented:** the shared replace-only `AssetMode` type is required on
`MaterializationRequest`; the asset forwards its declaration explicitly.
`commit_strategy()` constructs a strategy from an immutable framework-owned
registry before database access or cache lookup. The pipeline no longer accepts
an injected strategy, and `AssetRecord` requires an explicit mode supplied from
the request. The existing replacement transaction and schema implementation are
unchanged. Future mode-specific settings require a separate design; no options
or public extension API are introduced here.

**Validation:** all 179 offline synthetic tests pass, including six new mode
cases covering default/explicit declaration, registry-selected publication,
persisted mode, replacement, cache reuse and unsupported-mode rejection before
source/storage access. Existing rollback, schema failure/retry and empty-run
tests also pass. Ruff lint, formatting of the seven changed Python files,
documentation links and `git diff --check` pass. The terminology scan found only
the existing historical locking observation and its epic explanation.
Pyright reports the same 13 pre-existing errors in `_api.py`, `normalize.py`,
`_query.py` and `_templates.py`; none concern mode propagation or the new tests.
Sandbox temporary-directory/dependency access failures were resolved by running
pytest and Pyright outside the sandbox with local dependencies and synthetic
data. No live services or credentials were accessed.

The phase remains In Progress because other register items remain open.

## EXT-07: Input adapters and compatibility

**Clarification, 26 October:** this item combines missing input support with a
proposed normalization refactor. It is not evidence that all normalization is
missing or that the existing implementation cannot be extended. Editing a type
dispatcher is not, by itself, an extensibility defect.

**Current implementation:** [normalize.py](../../../src/assquack/_materialization/normalize.py)
handles mappings, pandas DataFrames and sync/async iterables. Arrow tables/batches,
DuckDB relations and HTTP responses remain TODOs. Asset/result input resolution
also remains unimplemented.

**Direction discussed:** reuse conversion handlers through multiple resolution
paths; a handler converts and delegates or reaches a terminal writer. Keep Arrow
input support in scope rather than requiring an Arrow conversion for every input.
Do not mandate a complete ingestion strategy per type or public adapter injection.

**Still undecided:** exact dispatch and handler contracts, terminal writer paths,
and whether native COPY's approximate rollover is acceptable. The benchmark and
unapproved writing recommendation are recorded in the
[scope journal's 26 October amendment](../../journal/2026-09-08-mvp-scope.md).
No size-based switching threshold or normalization implementation was approved.
Future acceptance must cover the supported inputs, bounded processing, complete
records and JSON value semantics; preserve full mapping envelopes rather than
implicitly extracting a `data` field.

## EXT-08: Observation extension contract

**Evidence:** the scope calls for a practical observation extension point, but
the active pipeline offers no explicit observer hook. Obsolete observation
helpers were removed under EXT-05; no observation API is shipped.

**Decision needed:** what evidence is available, when observers run, who owns
their persistence, and whether an observer failure blocks publication.

**Proposed acceptance:** document that contract and prove any approved hook with
a synthetic observer and failure case. Keep detailed observation collection out
of the default MVP execution path. Do not infer that a generic event bus is needed.

## EXT-09: Catalog evolution

**Decision, 26 October - Deferred:** there are no released versions requiring
catalog compatibility. Do not implement versioned migrations or preserve earlier
unreleased catalog layouts merely to satisfy this review item. Future released
upgrade support requires a real requirement and separate scope.

The original evidence was an individual column migration in
[tables.py](../../../src/assquack/_storage/tables.py). That migration and its
upgrade test were removed in `cbc0150`; fresh catalogs include the active columns
directly. Fragment ownership and retention behavior were retained. No existing
user database was deleted or upgraded by this cleanup.

The separate observation that some metadata inserts depend on column order does
not establish a need for a migration framework. Fresh creation and ordinary
reopening remain tested current behavior; upgrading old layouts is not an MVP gate.

## Related MVP release gaps

### REL-01: Preserve successful exports during write failures

The writer copies directly to the final URI. It does not implement staged output
publication. The export phase explicitly records partial remote-write preservation
as unfinished. Distinguish a failed extraction, where export never starts, from
a failed export after the DuckDB table has committed.

Acceptance should test interrupted export writes, preservation of the prior
artifact, truthful manifests, temporary-artifact cleanup and successful retry.
Destination-specific publication guarantees need an explicit design; do not assume
all object stores provide local-filesystem rename semantics. Coordinate with EXT-03.

### REL-02: Reconcile exposed settings and supported behavior

[asset API](../../../src/assquack/_api.py) advertises JSON/CSV in `ExportFormat`,
while parsing rejects them. Rowan confirmed on 23 October that both formats are
required MVP capabilities, so these are implementation gaps rather than unresolved
format-scope decisions. [configuration](../../../src/assquack/_config.py)
exposes export options and snapshot settings that execution does not consume.
Agree whether to implement, reject or defer each setting, then align types,
validation, documentation and tests. Do not silently accept nonfunctional switches
or expand MVP scope merely because a field already exists. Coordinate with EXT-04.

### REL-03: Complete release evidence

The export phase reports 73 targeted offline tests passing for the earlier Azure
slice, not a fresh complete-suite result. It also records existing repository-wide
type errors and no live Azure authentication/write verification. Recheck those
claims at release time and reconcile phase checklists with actual implementation.

Acceptance includes relevant offline tests, repository lint/type checks, docs
checks and representative migration fixtures. Live customer systems or credentials
require separate explicit permission; mocked tests cannot establish live success.

## Working through the register

For each item, record the agreed scope, owning agent/chat, affected files,
acceptance evidence and remaining limitations here when Rowan commissions it.
Resolve public-contract questions before dispatching implementation. Suggested
grouping is EXT-01/02, EXT-03/04 with REL-01/02, EXT-05/08, EXT-06, EXT-07,
and EXT-09; this is dependency guidance, not an instruction to launch parallel work.

An item closes only when its agreed acceptance criteria are verified and relevant
developer docs and phase status are updated. A documented decision to defer an
item must retain the limitation and release rationale. Do not mark a boundary
complete solely because a protocol, folder or mocked internal substitution exists.

## Related Docs

- [Documentation index](../../README.md): entry point for this register.
- [Scope journal](../../journal/2026-09-08-mvp-scope.md): authoritative release boundary.
- [Roadmap](../../roadmap.md): later features these boundaries should accommodate.
- [Replace materialization](02-replace-materialization.md): schema and mode work.
- [Export compatibility](04-export-compatibility.md): export implementation evidence.
- [Developer API](../../developer-api.md): contracts to preserve and document.
