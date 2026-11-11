# Export Compatibility

Status: **In Progress**
Last updated: 2026-11-11
Epic: 01 MVP
Phase: 04
Related docs: [Roadmap](../../roadmap.md), [Developer API](../../developer-api.md), [Exports](../../exports.md), [Storage Model](../../storage-model.md)

## Intent

Write compatibility artifacts from committed DuckDB tables without letting file
exports become the canonical asset state.

## Scope

Included:

- Export alias resolution for paths and file type aliases.
- Pipe-separated multi-format exports, including path and type-only declarations
  (Rowan decision, 23 October; MAD.Prefect compatibility).
- Parquet, CSV and JSON current-state exports, with Parquet as the default
  (Rowan decision, 23 October).
- `mad://` logical URI resolution to configured export base.
- `assquack_meta.exports` manifest rows for successful exports.
- ADLS/ABFSS export path support through native DuckDB.
- Azure credential-chain secret registration, with optional explicit chain selection.
- Azure `auto` selects complete credential-chain inputs first, then a complete
  SAS environment pair; explicit methods require their own inputs (6 November).

Excluded:

- Treating exports as cache truth.
- Durable raw JSON export as the default storage path.
- fsspec fallback and Prefect credential-block integration for MVP exports.
- Arbitrary connection strings, account keys and credential-store/block integrations.

## Architecture Notes

### Native Azure export exceptions — 11 November 2026

Implemented and independently reviewed: the user superseded the earlier
diagnostic projection with native exception passthrough. Removed the classifier
and masking tests. Preserve the
original exception object, type, message, traceback, notes and cause/context chains
from extension loading, credential registration, writing and cleanup. Keep
temporary-secret cleanup and captured-input release. A secondary cleanup failure
is logged with its native traceback while the original write failure propagates.

Downstream applications own access control and any required log redaction.
Assquack does not explicitly log credential inputs or authentication SQL, but
does not alter native diagnostic payloads. Offline tests must verify passthrough
at every stage, unknown native details, exception groups, cleanup precedence and
the existing no-success-manifest behaviour. Extension installation and deployment
configuration remain outside this change.

Validation: all 172 focused offline export tests passed, including identity,
message, native traceback, notes and chain preservation, real offline extension
loading failure, exception groups, cancellation, cleanup precedence and manifest
behaviour. The reviewer independently passed five native passthrough probes with
no production findings. Ruff, changed-file formatting, Pyright, Markdown links and
`git diff --check` passed. The full suite was not repeated: its prior unrelated
NumPy/pytz dependency gaps remain. No live calls or credentials were used. This
change is complete; the phase remains In Progress for existing live acceptance.

### Environment-only credential sourcing - 9 November 2026

Implemented: removed the injected credential-provider interface and asynchronous
loading bridge. The single-destination MVP has no demonstrated non-environment
credential-source requirement. Resolution reads selected inputs directly from the
process environment once, retaining validation, captured values, fingerprints and
operation cleanup. Authentication methods remain extensible. Earlier provider
injection notes below are historical and superseded.

Validation: 465 offline tests passed, followed by 185 independent focused tests
after the final test revision. Ruff lint, Pyright, changed-file formatting,
documentation links and `git diff --check` passed. Paired review has no remaining
findings. Whole-repo formatting still flags pre-existing pipeline comment indentation;
that unrelated file is unchanged. No live credentials or calls used.
The broader phase remains In Progress for its existing live acceptance work.

### Single destination amendment — 9 November 2026

Completed: removed simultaneous multi-destination exports for MVP.
`AssquackConfig.exports` accepts one path, destination instance or filesystem
mapping; `None` disables automatic exports. Lists are rejected. Multiple formats,
authentication, fingerprints, snapshots and cached-table export reattempts remain.
Destination fan-out and cross-destination collision checks are removed while
operation-owned cleanup remains. Offline tests cover configuration forms,
multi-format partial failures, changed destinations/credentials between runs
and isolated operation state. This supersedes earlier single-or-list notes below.

Validation: all **469 tests passed** on Python 3.11.9 / DuckDB 1.5.6; an independent
reviewer also passed 125 targeted tests and reviewed correctness and readability.
Ruff lint/format, Pyright (zero errors/warnings), documentation links and whitespace
checks passed. The initial suite exposed one old list-shaped test fixture, now
fixed. Pyright needed access to existing installed dependency metadata; no live
systems or customer data were used. The broader phase remains **In Progress**
because its existing end-to-end acceptance work is outside this slice.

### Resolve/export amendment — 27 October 2026

Implementation is in progress. `FileSystemDestination.resolve()` resolves all
write paths and formats, selects preparation and authentication through their
registries, captures required inputs once, and fingerprints the resolved
destination configuration. Private operation state retains these selections.
`FileSystemDestination.export()` uses that state and calls `DuckDBFileWriter`.
There is no plan object or replacement execution API.

An operation owns its destination instances and releases captured state on cache
hit, success, and failure. Preparation cannot change the resolved writer path.
The collision dictionary maps each resolved write path to its destination
fingerprint and rejects a different fingerprint at that path. Manifest/cache
matching uses both values; output formats and individual filenames are excluded
from the destination fingerprint, and asset identity remains unchanged.

Rowan's environment-chain decision is implemented: resolution captures and
fingerprints the supported environment input values, requires one complete
authentication method, and restricts DuckDB to `env`, `workload_identity`, or
both in order. No CLI or managed-identity fallback is allowed. Token acquisition
still happens during DuckDB execution; token-file contents and issued tokens
are not fingerprinted. See [supported inputs](../../configuration.md#credential-chain-environment-inputs).

Validation for this amendment: full offline suite **228 passed**; focused export
type checks, touched-file lint, documentation links, and whitespace checks pass.
Azure calls were mocked; no live credentials or customer systems were accessed.

Model export writers as a narrow strategy boundary around the committed current
table. Export formats and URI schemes should not leak back into asset identity
or materialization state.

The [22 October scope decision](../../journal/2026-09-08-mvp-scope.md#6-native-export-path-and-aliases)
settles native Azure authentication: register temporary secrets on the export
connection, using `credential_chain` for SDK discovery or `config` for the
approved SAS amendment. Credential loading is separate from registration.
See [ADLS credentials](../../configuration.md#adls-credentials). The authentication and native Parquet routing slice is implemented with offline
verification; live Azure verification and the broader phase remain incomplete.

## Implementation Checklist

- [x] Implement export target parsing and alias resolution.
- [x] Implement Parquet export from committed current table.
- [x] Implement CSV and JSON writers through the format-selection boundary.
- [ ] Verify all three formats against representative offline MAD.Prefect
  compatibility fixtures for local and Azure export paths; record live checks
  separately from mocked Azure coverage.
- [x] Record canonical `assquack_meta.exports` manifest rows.
- [x] Register the Azure credential-chain secret and support optional chain selection.
- [x] Add targeted checks for secret registration on the export connection and
  credential-selection configuration; distinguish offline checks from live verification.
- [x] Add tests for file type aliases, pipe syntax, and resolved URIs.
- [x] Expand pipe declarations before writer/destination selection; verify one
  extraction, all requested outputs, per-output manifests and partial-failure behavior.
- [x] Add tests proving export failures do not corrupt successful table state.
- [x] Update this status header before handoff.

## Validation

- `poetry run pytest`
- `poetry run pyright`
- `poetry run ruff check .`
- `poetry run ruff format --check .`
- export integration checks selected during implementation
- `git diff --check`

## Notes

Replacing MAD.Prefect data-asset workflows guides scope decisions. The 23 October
format decision adds CSV and JSON to required capabilities, without adding a core
dependency or automatically accepting every legacy option. Exact JSON layout and
supported options need reference review. This documentation update does not mark
either writer implemented; the phase remains In Progress.

Exports are an adapter surface around DuckDB tables. Keep the implementation
behind a small writer strategy so additional formats do not leak into the asset
decorator.

## 22 October Implementation

- Added `exports.azure_chain` and `exports.azure_account_name`, with process-env
  aliases and validation; default discovery leaves `CHAIN` unspecified.
- Native Azure Parquet routing is shared by pipeline and explicit result exports.
  The writer loads the provisioned extension, creates a unique directory-scoped
  temporary secret on the COPY connection, and drops it on success or failure.
- Registration is a small authentication context manager, available for later
  methods without adding unsupported configuration now.
- Database setup now closes its connection if settings, extension loading or
  bootstrap fails. Export failures preserve committed table/run metadata and
  do not create a false successful manifest.
- Targeted offline checks cover config precedence, SQL binding/parsing, routing,
  secret cleanup, both entry points, failures and real local Parquet output.
  Installed DuckDB is 1.5.2; Azure extension is absent. No credentials or live
  storage were accessed. Mocks do not establish live authentication correctness.
- Phase remains **In Progress**: remote COPY staging/promotion on write failure,
  broader alias/format behavior and live Azure acceptance remain outstanding.

### Verification Results

- `poetry run pytest tests/test_azure_exports.py tests/test_exports.py tests/test_config.py -q`:
  **28 passed**; repeated independently by the reviewer. Offline only.
- `poetry run ruff check src tests`: passed.
- Ruff format check on the seven changed Python files: passed.
- Pyright on the seven changed Python files, using the Poetry interpreter:
  **0 errors**. Repository-wide Pyright still reports pre-existing issues in
  `_api.py`, `_materialization/normalize.py`, `_query.py`, and `_templates.py`;
  these unrelated typing issues were not expanded into this change.
- Documentation link/anchor check and `git diff --check`: passed.
- Terminology scan: only the existing historical cache observation
  and its explanatory epic reference; no new terminology violations.
- Sandbox blocked pytest temporary files and Pyright dependency discovery;
  those checks succeeded with escalation for offline local execution.

## 6 November Authentication Input Selection Amendment

Status: **Complete** for this amendment; the broader phase remains **In Progress**.
Rowan approved replacing the 5 November policy below.
Each authentication registration declares acceptable input combinations and any
optional inputs. Shared `has_required_inputs(available_inputs,
acceptable_input_combinations)` requires every input in one combination and
accepts any complete combination. Registry order gives the credential chain
priority over SAS. Explicit methods use the same check and report required inputs
without switching methods. Selected authentication failure never tries another
strategy. Provider loading and each input read happen once per resolution;
strategy-specific SAS validity checks, redaction, fingerprints and credential
lifetime remain intact. Local `auto`/`none` do not load credentials.

Offline validation covers priority, explicit overrides, missing/partial inputs,
chain alternatives, one provider load, no runtime failover, local exports and
existing credential lifetime and redaction behavior. All 176 scoped offline tests
pass. Ruff lint/format, Markdown links and whitespace checks pass. Pyright reports
no changed-file errors; its seven existing errors remain in untouched files. The
walkthrough identifies Kubernetes as downstream infrastructure, not a core dependency.
No live Azure services, credentials or token files were accessed.

## 5 November Authentication Selection Amendment (superseded)


Azure `auto` now selects the existing credential-chain factory and never reads
the SAS pair. Explicit `sas` retains its existing validation and registration.
Supported environment, certificate and workload-identity chain behavior stays
unchanged; local `auto` still performs no authentication. Offline regression
checks cover SAS-pair independence, explicit SAS and both export entry points.
This supersedes the automatic selection described in the historical amendment
below. The phase remains In Progress for its existing export release gaps.

Validation: 125 targeted offline tests pass, as do Ruff lint/format, local
Markdown links and whitespace checks. Full Pyright reports seven existing errors
in untouched `_api.py`, `_query/asset.py` and `_templates.py`; none in the changed
files. No live services or credentials were used. Independent review approved
the implementation and narrative with no actionable findings and reproduced
the 125 passing tests, lint/format, links, whitespace and type-check results.

## 22 October SAS Amendment

Rowan approved adding SAS now. Credential providers supply explicit credentials;
authentication strategies decide how DuckDB registers them. The environment
provider reads only `AZURE_STORAGE_SAS_TOKEN` and `AZURE_STORAGE_ACCOUNT_NAME` at
Azure export time. Automatic selection uses a complete pair for SAS, no pair for
native credential discovery, and rejects an incomplete pair. Explicit strategy
selection overrides automatic selection. No block adapter or live verification
is included. Tests cover selection, redaction, URI/account consistency,
connection-string field safety, both export entry points and local regressions.

Implementation uses `AzureSasProvider.resolve()` returning validated
`AzureSasCredentials` with a `SecretStr` token. `CredentialChainAuth` and `SasAuth`
implement a narrow `AzureAuthStrategy.register()` boundary. The environment
provider is resolved only when automatic/SAS Azure authentication needs it.
`azure_auth` is the only new public configuration field. Explicit chain selection
and account contradictions fail before registration. No token is stored in
`ExportsConfig` or durable Assquack metadata. SAS backend errors suppress potentially
sensitive diagnostics, and cleanup warnings contain no exception details.

The Quackframe source references reviewed were `credential_loading/models.py`,
`credential_loading/providers/protocol.py`, `credential_loading/loading.py` and
`sql_functions/register_secret/models.py` in the local checkout. This informs
separation and secret handling only; Assquack imports none of that project.

Connection-string evidence was verified against the public
[DuckDB client implementation](https://github.com/duckdb/duckdb-azure/blob/main/src/azure_storage_account_client.cpp)
and [Azure C++ SDK parser](https://github.com/Azure/azure-sdk-for-cpp/blob/main/sdk/storage/azure-storage-common/src/storage_credential.cpp).
DuckDB checks `AccountName` for fully qualified URIs. The SDK accepts
`SharedAccessSignature`, uses the explicit HTTPS Blob endpoint and derives the
HTTPS DFS endpoint from `AccountName`. This is source verification, not a live
Azure authentication or write test.

### SAS Verification Results

- Targeted offline tests: **73 passed** across `test_azure_sas.py`,
  `test_azure_exports.py`, `test_config.py` and `test_exports.py`. The paired
  reviewer independently passed the preceding 70-case set; three additional
  endpoint/container validation cases also pass in the final set.
- Tests replace the provider's imported environment-read boundary with synthetic
  mappings, avoiding real SAS credential reads. Azure SQL is intercepted while
  real local DuckDB tables, lifecycle and manifests are exercised.
- Changed-file Pyright: **0 errors** across nine Python files. Existing unrelated
  repository-wide type errors noted above remain outside this scope.
- Ruff lint (`src tests`), changed-file formatting, documentation links/anchors
  and `git diff --check`: passed. Terminology scan remains unchanged, with only
  the two historical cache/locking references already documented above.
- Paired code/documentation re-review found no actionable defects after the
  stale SAS-deferral wording was corrected.
- No credentials, `.env`, customer data or live storage were read. No full test
  suite, commit or push. Native SDK syntax was checked against upstream source;
  installed DuckDB remains 1.5.2 without its Azure extension, so real SAS/identity
  authentication and Azure writes are unverified. Phase remains **In Progress**
  for the broader export verification work.


## Pipe formats implementation — 23 October 2026

The built-in Parquet, CSV and JSON slice is implemented. Path and type-only pipe
aliases expand before extraction, then write the same committed table in declared
order. Each completed output gets its actual format, role and fixed serialization
options in the manifest. Cache qualification requires every requested resolved URI
on the same latest successful run. A later export failure preserves earlier
manifests and canonical table state; retry extracts afresh.

The local MAD.Prefect `data_artifact.py` reference establishes NDJSON for JSON and
one CSV header without an index. Synthetic tests decode actual output bytes,
exercise nulls, Unicode, quoted multiline text and empty tables, and intercept
Azure COPY/secret setup without contacting Azure. Custom options are rejected;
The then-current `export_parquet()` convenience API is superseded by the
[4 November redesign](#destination-driven-manual-exports-4-november-2026).

The [27 October scope decision](../../journal/2026-09-08-mvp-scope.md#duckdb-only-export-boundaries-27-october-2026)
drops writer/adapter replacement registration. Destination composition,
authentication extensibility and live Azure verification
remain separate work. The phase remains In Progress.
Validation: 96 targeted offline tests passed across `test_pipe_exports.py`,
`test_exports.py`, `test_cache.py`, `test_azure_exports.py` and `test_azure_sas.py`.
Targeted Ruff lint/format and Pyright checks passed (zero type errors), as did the
Markdown link check, touched-doc terminology scan and `git diff --check`.
The separate visible review chat signed off with no remaining actionable feature
findings after independently checking the code, docs and the earlier 94-test
selection. Two final tests cover nested JSON and rejected custom options.
No complete-suite or live Azure result is claimed.

Windows sandbox temporary-directory permissions prevented the first pytest run;
the same targeted synthetic checks passed using the existing Poetry environment
outside the sandbox. No credentials or live systems were accessed.

## Destination refactor — 27 October 2026

The destination refactor is complete and independently reviewed. Configuration
accepts one `FileSystemDestination(base_uri, auth)` or a list; `[]` disables
automatic exports without an error. Registered preparation and authentication
are composed by the destination, which calls the shared DuckDB file writer.
Earlier `ExportsConfig`/Azure configuration details in the dated implementation
notes above are superseded by [current configuration](../../configuration.md#exports).

Multi-destination formats, explicit exports, manifests, cache qualification,
internal async provider substitution and cleanup are covered. The full offline
suite passes 198 tests, focused Pyright has no errors, and lint/format/docs/diff
checks pass. The paired reviewer reports no unresolved actionable in-scope
findings. See [EXT-01 through EXT-04](06-extensibility-defects.md#ext-01-azure-authentication-strategy-registry)
for implementation and validation evidence.

This phase remains In Progress. Live Azure/ADLS verification remains outstanding,
and the 13 unrelated repository type errors reported at this checkpoint are unchanged.

### Temporary export files — 28 October 2026

The shared writer now explicitly sets `USE_TMP_FILE true` for Parquet, CSV and
JSON exports. DuckDB writes a temporary file before replacing the destination
after successful completion. This makes the intended behavior explicit rather
than relying on `auto`. The earlier claim that direct COPY lacked this protection
was unsupported and is withdrawn; it is not an outstanding implementation defect.
No separate staging/promotion system is required. See [Exports](../../exports.md)
for the filesystem-dependent replacement contract.

Validation for this change: focused Ruff, Markdown links and whitespace checks
passed. The focused export test run reached 5 passing tests, with 48 setup errors
from temporary-directory permissions. An unsandboxed retry was blocked by
automatic approval review. Scoped Pyright could not resolve the installed DuckDB
import with its selected interpreter. No live storage checks were performed.

## Resolve/export checkpoint validation — 27 October 2026

The local/SAS and generic explicit-input implementation is complete enough for
review, but this feature is **not complete**. The user must choose native
credential-chain behavior: reject it until full capture is supported, or explicitly
accept configuration-only fingerprinting without native token-rotation detection.
The working implementation currently raises an explicit pending-decision error for
native fingerprints; this placeholder is not an approved final policy.

- Full offline suite excluding native-related cases: **196 passed, 14 deselected**.
- Focused type checks on changed source/tests: **0 errors**.
- Ruff lint across source/tests: passed. Changed Python formatting: checked
  separately; the broad check flags pre-existing formatting in untouched
  `tests/test_azure_sas.py`.
- Documentation link/anchor and whitespace checks: passed; terminology scan only
  finds the two pre-existing historical references.
- Paired review found and corrected cleanup on a rejected stale target and
  captured authentication retained by caught exception frames. Regression tests
  cover both, resolution failures, non-Azure inputs, cache/metadata, credential
  rotation, single retrieval, interleaving and temporary-secret cleanup.

All tests use synthetic credentials and local temporary DuckDB files. No live
systems, credentials, commits or branch changes were used. Existing uncommitted
work is preserved against the external baseline manifest.

## Plain export-path configuration — 30 October 2026

Implemented `AssquackConfig(exports="E:/data/namely/exports")` and the equivalent
plain `ASSQUACK_EXPORTS` value. The field accepts `str` and normalizes it to one
`FileSystemDestination(base_uri=value)`, retaining the destination's existing
`auth="auto"` default. Pydantic-settings' union decoding already allows raw
strings through while decoding JSON objects/lists; no custom decoder is needed.

Destination instances, JSON object/list environment forms, explicit auth,
direct-value precedence and `exports=[]` remain supported. Regression tests in
`tests/test_config.py` cover Windows slash/backslash and relative paths plus
those existing forms. Focused config/export tests: **47 passed** on DuckDB 1.5.6.
The configuration fix is complete; the overall phase remains In Progress for
its unrelated outstanding work. See [configuration usage](../../configuration.md#exports).

## Destination-driven manual exports — 4 November 2026

Status: **In Progress**. Implements DF-005 and the approved result API redesign.
The [manual contract](../../exports.md#manual-exports) defines deterministic
path/format defaults before implementation. Add optional declaration resolution
for destination-owned targets, and typed filesystem `path`/`format` fields.
Automatic declarations remain authoritative; `None` still disables automatic
exports at the pipeline boundary. Keep destination fingerprints tied to effective
automatic configuration rather than unused manual defaults.

Move ordered writes, cache skips and manifest recording into `_exports`, alongside
manual coordination. Results delegate to that module. Remove only the redundant
manual writer file lock; retain `database_operation` connection coordination.
Do not add a compatibility shim or database exporter. Prove generic destinations,
all local formats, synthetic Azure auth, partial failures, retries and cleanup.
DF-002 publication/provenance and cache identity remain separate, unresolved work.

Implementation and offline checks are complete; visible paired review is pending.
The broader phase remains **In Progress** for its existing acceptance items.

- Final export/publication/config/cache selection: **189 passed**.
- Full offline suite before updating one moved-helper test: **448 passed,
  8 skipped, 2 failed**. The moved-helper failure passes in the final selection.
  The remaining unrelated failure requires missing NumPy; skips require missing
  optional pandas/PyArrow dependencies.
- Changed-file Pyright: **0 errors**. Full Pyright reports seven existing errors
  in untouched `_api.py`, `_query/asset.py` and `_templates.py`.
- Ruff lint/format, documentation links and `git diff --check`: passed.
  Terminology scan retains only existing historical references.
- Synthetic provenance probe confirms DF-002 is unchanged: two current rows are
  exported while the older result records its original one-row manifest.
- Azure auth/writes were intercepted with synthetic values. No live credentials,
  customer systems, `.env`, branch changes, commits or pushes were used.
- Windows sandbox pytest temp-directory permissions required an approved offline
  escalated run. Evidence and commands are in the local review handoff under
  `.tmp/result-export-implementation/`.
