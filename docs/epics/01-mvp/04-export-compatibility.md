# Export Compatibility

Status: **In Progress**
Last updated: 2026-10-23
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
- SAS authentication from the explicit environment pair, with automatic selection
  and explicit authentication overrides (Rowan amendment, 22 October).

Excluded:

- Treating exports as cache truth.
- Durable raw JSON export as the default storage path.
- fsspec fallback and Prefect credential-block integration for MVP exports.
- Arbitrary connection strings, account keys and credential-store/block integrations.

## Architecture Notes

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
  for the broader export work, including partial remote-write preservation.


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
`export_parquet()` remains a single-format convenience API.

Writer registration, destination/authentication registries, staged publication,
and live Azure verification remain separate work. The phase remains In Progress.
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
