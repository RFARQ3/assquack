# MVP Extensibility Defects

Status: **In Progress**
Last updated: 2026-10-29
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

Fingerprint ownership clarified on 27 October: `destinations/base.py` owns
resolved-state capture on `ExportDestination`, plus exception-frame cleanup.
`destinations/fingerprint.py` owns fingerprint input assembly, deterministic
serialization, and hashing. `FileSystemDestination.resolve()` coordinates target
parsing, preparation/authentication selection, `_resolve_file_paths()`, and
fingerprint generation. File-path operations and base-path normalization live
in focused filesystem methods. One fingerprint is calculated per resolved
destination configuration, independently of pipe-expanded output paths.
The base destination owns the captured state and releases it through `_release()`.
`FileSystemDestination.resolve()` passes the completed fingerprint, preparation,
authentication, and targets to the inherited state-capture method.
Filesystem path resolution stays in the filesystem destination;
`models.py` contains export data shapes.

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

### Agreed export design — 27 October 2026

The [scope decision](../../journal/2026-09-08-mvp-scope.md#duckdb-only-export-boundaries-27-october-2026)
retains `FileSystemDestination(base_uri, auth)` under `ExportDestination`, leaving
room for future database destinations. `FileSystemDestination` coordinates
preparation and authentication through their registries and calls
`DuckDBFileWriter` directly. Preparation classes and authenticators remain
separate composed objects. No `DuckDBFileSystemAdapter` wrapper is needed;
DuckDB still supplies filesystem access and writing. Interchangeable writer and
adapter registries or injection are deliberately out of scope. Registry-based
preparation and authentication selection must not become if/elif dispatch.

The broader design received detailed consideration and is preserved in commit
`8bacb3d`. It was reduced because replacement machinery overcomplicates the
product and conflicts with its DuckDB-native goal. The general substitution
acceptance rule above does not apply to writers or filesystem adapters.

Authentication methods remain extensible. Internal credential-provider injection
remains to support future multi-destination exports whose credentials may come
from different sources. Public provider configuration is deferred; providers do
not settle per-destination credential selection on their own.

This direction is implemented and verified in the working tree; paired review
has no unresolved actionable findings.
REL-01 publication safety remains separate required work. See the
[source research](../../research/2026-10-26-duckdb-filesystem-preparation.md)
for setup differences and the unresolved ADLS parent behavior.

Pipe-format implementation is assigned to the visible implementation chat, with
independent review in the visible review chat. This slice covers built-in formats,
target expansion, truthful manifests and all-target cache qualification. Destination composition and authentication extensibility are implemented in the
27 October refactor below; writer registration is not required.
Priorities below are review recommendations, not a revised approved release scope.

| ID | Finding | Disposition | Status |
|---|---|---|---|
| EXT-01 | Azure authentication selection | Instance-owned compatible-auth registry implemented | Complete |
| EXT-02 | Export credential-provider injection boundary | Internal async load/get injection implemented; public API and integrations deferred | Complete |
| EXT-03 | Destination routing | Destination contract and registered preparation implemented | Complete |
| EXT-04 | Export writing and metadata | Shared DuckDB writer and multi-destination manifests/cache verified | Complete |
| EXT-05 | Schema strategy selection and context are insufficient | Internal registry and argument-free resolution implemented | Complete |
| EXT-06 | Materialization mode does not reach execution or metadata | Internal registry and mode propagation implemented | Complete |
| EXT-07 | Input support gaps and normalization design | Missing features separated from proposed refactor; writing path undecided | Open |
| EXT-08 | Observation extension point is undefined | Design decision required | Open |
| EXT-09 | Catalog evolution has no versioned migration mechanism | Earlier unreleased catalogs need no compatibility; future upgrades deferred | Deferred |
| REL-01 | Export write failure preservation is incomplete | Existing export acceptance work | Open |
| REL-02 | Public export settings/types exceed implemented behavior | Destination configuration reconciled; unsupported fields rejected | Complete |
| REL-03 | Release validation evidence is incomplete | Existing release acceptance work | Open |

## Agreed export source layout

**Implemented and reviewed 27 October 2026.** The working tree uses this
layout. The phase remains In Progress because unrelated register and release
items remain open.

```text
src/
└── assquack/
    └── _exports/
        ├── __init__.py              # Export package entry points
        ├── models.py                # Export targets and output metadata
        ├── targets.py               # Alias and pipe-format expansion
        ├── destinations/
        │   ├── __init__.py          # Expose destination classes
        │   ├── base.py              # ExportDestination contract
        │   └── filesystem.py        # FileSystemDestination: prepare, then write
        ├── preparation/
        │   ├── __init__.py          # Preparation package entry points
        │   ├── registry.py          # Select preparation strategy by URI scheme
        │   ├── local.py             # Resolve paths and create parent directories
        │   └── azure.py             # Resolve Azure URIs and load DuckDB extension
        ├── authentication/
        │   ├── __init__.py          # Authentication package entry points
        │   ├── base.py              # Authenticator lifecycle contract
        │   ├── registry.py          # Select compatible authentication methods
        │   ├── local.py             # No-op local authentication
        │   ├── azure.py             # SAS and credential-chain authentication
        │   └── credentials.py       # Internal providers; environment default
        └── writer/
            ├── __init__.py          # Internal writer entry points
            └── file.py              # DuckDBFileWriter: COPY to Parquet, CSV or JSON
```

The destination owns export coordination, not serialization. It obtains the
preparation strategy and compatible authenticator from their registries, enters
the composed preparation context, and invokes the separate `DuckDBFileWriter`.
Preparation classes own local/Azure setup; authenticators own authentication
resource lifetimes. The writer performs shared COPY with validated format options.
Both automatic and explicit exports must use this composition.

`destinations/filesystem.py` calls `writer/file.py` directly. No writer registry,
engine injection or adapter wrapper is introduced. A future database destination
and DuckDB table writer would live in `destinations/database.py` and
`writer/table.py`; do not create them now. Their configuration, target-path
interpretation and write semantics need a separate design. Different destination
types implement the common `ExportDestination` contract, allowing shared export
coordination without destination-type branching or file-format assumptions.

## EXT-01: Azure authentication strategy registry

The generic `Authenticator` protocol requires only fingerprint inputs and
authentication. `account_name` belongs exclusively to `AzureAuthenticator` and
its Azure implementations. Azure preparation uses that Azure-specific contract;
local and independently registered non-Azure authenticators need no account field.
The synthetic registered-authenticator regression exercises this without an
`account_name` attribute.

**Complete — 27 October 2026.**
[AuthenticationRegistry](../../../src/assquack/_exports/authentication/registry.py)
selects factories by preparation kind and authentication name. Supplied choices
are local `auto`/`none` and Azure `auto`/`sas`/`credential_chain`. Automatic Azure
selection retains complete/absent/partial SAS-pair semantics. Native identity
selection loads and validates the environment credential inputs. Unknown/incompatible names fail;
duplicate registration fails; each filesystem destination owns its registries.

The authenticator context owns temporary-secret registration and cleanup.
[Azure authentication](../../../src/assquack/_exports/authentication/azure.py)
uses safe failures without concrete-strategy type checks. Synthetic registered
authentication is exercised through automatic and explicit execution, including
cleanup. No new authentication integrations are supplied.

## EXT-02: Export credential provider injection

**Complete — 27 October 2026.**
[CredentialProvider](../../../src/assquack/_exports/authentication/credentials.py)
defines asynchronous `load()` and synchronous `get(name)`. A destination's private
`_credential_provider` accepts a provider instance and defaults to
`EnvironmentCredentialProvider`. No public provider field, provider registry,
Prefect block or vault integration is added.

Synchronous destination resolution runs loading to completion. If the caller already
has a running event loop, a worker thread runs a separate loop and the caller
waits for it. Providers must not depend on work scheduled on the blocked caller
loop. Tests exercise this boundary through both automatic and explicit exports,
including an explicit export inside an async function. Each destination may have
its own injected instance. Local exports never load
or retrieve SAS values. Provider failures suppress sensitive diagnostics and no
provider is included in public serialization or representations.

## EXT-03: Destination strategies

**Complete — 27 October 2026.**
[ExportDestination](../../../src/assquack/_exports/destinations/base.py) defines
`resolve()` and `export()` without filesystem-format assumptions.
Config accepts one destination or a list, normalizing to a list; mappings and
`ASSQUACK_EXPORTS` JSON construct the supplied filesystem destination. Empty lists
disable automatic exports and manifest cache requirements.

[FileSystemDestination](../../../src/assquack/_exports/destinations/filesystem.py)
uses `resolve()` to select registered preparation and authentication, capture
required inputs once, freeze paths/formats, and fingerprint destination configuration.
Private state is operation-owned. Its `export()` uses those selections, enters the
preparation context and calls `DuckDBFileWriter` directly. Preparation owns setup;
authentication owns temporary secrets. There is
no adapter wrapper, writer registry or alternative-engine injection.

Both registries are instance-owned and reject duplicates and unknown selections.
Synthetic preparation/authentication uses real DuckDB COPY without changing the
pipeline. A synthetic non-file destination demonstrates independent declaration
interpretation and manifest/cache participation. Local/file and Azure support
remain supplied scope. Database output and remote staged publication are deferred.

## EXT-04: Export writers and manifest data

**Complete — 27 October 2026.**
[DuckDBFileWriter](../../../src/assquack/_exports/writer/file.py) owns shared COPY
with fixed Parquet, CSV and NDJSON options. Formats expand per destination before
execution. Both export paths record successful output format, role, options and
resolved URI and destination fingerprint; failures stop remaining writes without
recording false successes. Cache qualification requires every resolved write path
and fingerprint on the latest successful run. A plain operation dictionary rejects
the same write path only when fingerprints differ. No remote alias detection is
performed. The fingerprint describes the destination, not each output format/path.

Destinations execute in configuration order, each in format declaration order.
`export_parquet()` keeps Parquet-only semantics, returning a string for one output
or a list for multiple outputs. With no configured destinations it uses the
default local base and automatic authentication. Unsupported old configuration
fields are removed/rejected rather than silently accepted.

### Resolve/export follow-through — environment chain decision implemented

On 27 October Rowan approved fingerprinting the supported credential-provider
inputs present in the deployment environment. Require one complete client-secret,
certificate, or workload-identity set. Explicitly restrict DuckDB's chain to
`env`, `workload_identity`, or both in that order; do not use CLI or managed-identity
fallback. Capture input values once during resolution and retain redacted private
state until completion or failure. Token acquisition remains with DuckDB.
This fingerprints configuration values, not the issued token or contents of
certificate/token files. See [configuration](../../configuration.md#credential-chain-environment-inputs).
Offline tests cover complete and incomplete input sets, explicit chain SQL,
provider loading, fingerprint changes, and cleanup. Live Azure verification is
not part of these checks. The broader phase remains In Progress.

### Validation and paired review — 27 October 2026

Implementation chat: `01a17af5-c7e9-7e90-bd6e-702f4aa1bda3`.
Read-only reviewer chat: `01a17af5-d370-78e0-9765-cfe4f66075fa`.
Independent re-review found no unresolved actionable in-scope findings after
fixes for single-destination constructor typing/JSON schema, equivalent URI
collisions, absolute local paths under remote bases and historical source links.

- Full offline synthetic suite: `poetry run pytest -q` — **198 passed**.
- Focused Pyright over `_exports/`, `_config.py`, `_result.py`, pipeline and all
  affected export/config/cache tests — **0 errors, 0 warnings**; independently
  confirmed by the reviewer.
- `poetry run ruff check src tests` and `poetry run ruff format --check src tests`
  pass. Documentation link/anchor validation and `git diff --check` pass.
- Terminology scan found only the existing historical locking observation and
  its epic explanation.
- Full `poetry run pyright` retains the same **13 pre-existing errors** in
  `_api.py`, `normalize.py`, `_query.py` and `_templates.py`. No refactor-specific
  type errors remain. Sandbox restrictions on temporary directories and Python
  subprocess discovery required offline checks outside the sandbox.

Coverage includes local/file and intercepted Azure execution, all three formats,
multi-destination success/failure/retry and manifests, all-target cache checks,
empty destination lists, registry duplicate/unknown/incompatible choices and
isolation, synthetic preparation/authentication and non-file destinations,
per-destination asynchronous provider substitution, active-loop explicit exports,
secret cleanup and safe diagnostics. No live credentials or systems were used.

EXT-01 through EXT-04 and REL-02 are complete for the approved refactor.
The phase remains In Progress: REL-01 direct-COPY preservation, REL-03 live Azure
and broader release evidence, unrelated typing failures and other open register
items remain separate work. ADLS parent creation is unverified. No commit, branch,
push or PR was created.

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

**Implemented direction, 29 October:** [fragments.py](../../../src/assquack/_materialization/fragments.py)
owns input dispatch and two shared writers. Python mappings use buffered bytes;
relations use native COPY. Pandas DataFrames and Arrow Tables/RecordBatches become
relations. Sync/async yields dispatch each item, including mixed input types.
Result objects read their materialized table; asset objects execute with their
normal cache behavior before following that result route. Same-task nested
execution shares the database lock without admitting other tasks.

This approval supersedes the earlier undecided handler/terminal-writer direction
and direct HTTP-response TODO. Native COPY's approximate rollover, including
substantial overshoot, is accepted. Authors decode HTTP JSON themselves. No
per-type strategy classes, public adapter registry, size-switching threshold or
mandatory Arrow conversion is introduced. Historical benchmark evidence remains
in the [scope journal](../../journal/2026-09-08-mvp-scope.md).

Focused validation passed 101 tests across input routing, real COPY output and
failure, empty outputs, retention, nested execution/cache, schema, reader recovery
and exports. Targeted Pyright passed. See the
[replace-materialization phase](02-replace-materialization.md)
for implementation status and final review results.

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

The 27 October refactor removes `ExportsConfig` and its unimplemented options
and snapshot fields. `FileSystemDestination` accepts only `base_uri` and `auth`;
unknown fields fail validation. Parquet, CSV and JSON all use the shared writer.
The destination review and acceptance checks verify this reconciliation.
Snapshots, custom COPY options and public provider injection remain deferred.

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
