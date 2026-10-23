# MVP Extensibility Defects

Status: **In Progress**
Last updated: 2026-10-23
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

Pipe-format implementation is assigned to the visible implementation chat, with
independent review in the visible review chat. This slice covers built-in formats,
target expansion, truthful manifests and all-target cache qualification. Broader
writer registration and destination/authentication registries remain open.
Priorities below are review recommendations, not a revised approved release scope.

| ID | Finding | Disposition | Status |
|---|---|---|---|
| EXT-01 | Azure authentication selection is hardcoded | Proposed pre-release repair | Open |
| EXT-02 | Credential providers cannot be supplied through normal exports | Proposed pre-release repair | Open |
| EXT-03 | Parquet writing owns destination routing | Proposed pre-release repair | Open |
| EXT-04 | Export format selection and metadata are hardcoded | Proposed pre-release repair | Open |
| EXT-05 | Schema strategy selection and context are insufficient | Proposed pre-release repair | Open |
| EXT-06 | Materialization mode does not reach execution or metadata | Proposed pre-release repair | Open |
| EXT-07 | Input conversion has no adapter selection boundary | Proposed repair; missing baseline inputs are already MVP work | Open |
| EXT-08 | Observation extension point is undefined | Design decision required | Open |
| EXT-09 | Catalog evolution has no versioned migration mechanism | Pre-release upgrade-policy decision required | Open |
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

## EXT-02: Credential-provider injection

**Evidence:** [azure.py](../../../src/assquack/_exports/azure.py) accepts a provider
in `azure_export_auth()`, defaulting to `EnvironmentSasProvider`.
[parquet.py](../../../src/assquack/_exports/parquet.py) never supplies one.
[azure_credentials.py](../../../src/assquack/_exports/azure_credentials.py)
contains the provider protocol, but normal asset/result exports expose no route
to inject a provider.

**Consequence:** an external provider cannot participate through the supported
execution path despite satisfying the internal protocol.

**Proposed outcome and acceptance:**

- Agree a supported provider-injection path, retaining the environment default.
- Prove both automatic asset exports and explicit result exports use a supplied
  synthetic provider, without monkeypatching or reading real credentials.
- Resolve credentials at use; keep secrets out of public serialized settings,
  metadata and diagnostics. Local exports must not invoke Azure providers.
- Keep optional orchestrator adapters outside core dependencies.

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

**Evidence:** [schema.py](../../../src/assquack/_materialization/schema.py)
hardcodes `current_run`; `resolve()` receives only observed columns/types.
[pipeline.py](../../../src/assquack/_materialization/pipeline.py) provides no
asset/history context. The substitution test in
[test_schema_replacement.py](../../../tests/test_schema_replacement.py)
monkeypatches the selector. A previous-successful-schema lookup already exists in
[metadata.py](../../../src/assquack/_storage/metadata.py).

**Consequence:** continuation cannot obtain previous schema through the present
contract, and replacing the policy requires changing internal selection code.

**Proposed outcome and acceptance:**

- Provide supported policy selection and explicit context or a history-access
  capability sufficient for a future continuation implementation.
- Keep current-run inference as the shipped default with no implicit historical
  reconciliation or unsupported continuation switch.
- Test a synthetic policy through normal per-call configuration, with access to
  the correct asset's successful history and without monkeypatching.
- Preserve current-run drift, missing-field and failed-refresh behavior.

## EXT-06: Materialization mode propagation

**Evidence:** [asset API](../../../src/assquack/_api.py) stores `mode`, but
[MaterializationRequest](../../../src/assquack/_materialization/models.py) omits
it. [pipeline.py](../../../src/assquack/_materialization/pipeline.py) defaults
directly to `ReplaceCommitStrategy`; [AssetRecord](../../../src/assquack/_storage/models.py)
independently defaults its stored mode to `replace`.

**Consequence:** adding another commit strategy is insufficient to select it
from the asset declaration or record its mode correctly.

**Proposed outcome and acceptance:**

- Carry mode consistently from declaration through execution and metadata.
- Define strategy selection and the boundary for future mode-specific settings.
- Verify selected strategy execution and accurate stored mode using synthetic
  tests; retain transactional replace behavior.
- Keep unsupported modes rejected. Append/merge algorithms, primary keys and
  snapshot publication remain outside this work unless separately approved.

## EXT-07: Input adapters and compatibility

**Evidence:** [normalize.py](../../../src/assquack/_materialization/normalize.py)
mixes type dispatch, conversion and recursive iteration. Arrow, DuckDB relations
and HTTP responses remain TODOs, although the scope includes those inputs.

**Consequence:** each new input type requires editing the central normalizer;
the agreed migration baseline is also incomplete.

**Proposed outcome and acceptance:**

- Separate input conversion from batching and define adapter selection priority,
  including precedence over generic iterable handling.
- Complete the agreed input types using offline representative fixtures.
- Prove an extra adapter can be supplied without editing the normalizer.
- Preserve sync/async batches, bounded output chunks and complete mapping
  envelopes; do not implicitly extract an envelope's `data` field.

## EXT-08: Observation extension contract

**Evidence:** the scope calls for a practical observation extension point.
[observation.py](../../../src/assquack/_materialization/observation.py) retains
helpers, but the active pipeline offers no explicit observer hook.

**Decision needed:** what evidence is available, when observers run, who owns
their persistence, and whether an observer failure blocks publication.

**Proposed acceptance:** document that contract and prove any approved hook with
a synthetic observer and failure case. Keep detailed observation collection out
of the default MVP execution path. Do not infer that a generic event bus is needed.

## EXT-09: Catalog evolution

**Evidence:** [tables.py](../../../src/assquack/_storage/tables.py) combines
bootstrap with an individual column migration, without a versioned migration
mechanism. Some inserts in [metadata.py](../../../src/assquack/_storage/metadata.py)
depend on table column order.

**Consequence:** future metadata additions need ad hoc upgrade handling and can
break existing databases. This is an upgrade-contract gap, not a requirement to
replace DuckDB or introduce arbitrary database backends.

**Decision and proposed acceptance:** agree the supported pre-release catalog
baseline and upgrade policy; define ordered, repeatable migrations and explicit
insert columns. Test fresh creation, supported old-catalog upgrades, repeated
opening, and preservation of existing metadata on migration failure.

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
