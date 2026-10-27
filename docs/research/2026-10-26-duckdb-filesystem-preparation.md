# DuckDB filesystem preparation for local and Azure exports

Research date: **26 October 2026**, Australia/Sydney. Research only; no application
changes or approval of a new public contract. Scope: single-file Parquet, CSV and
JSON exports using the repository's DuckDB version.

## Verdict

**One DuckDB writer and one internal DuckDB filesystem adapter are sufficient.**
There are real setup differences, so a small composed preparation boundary is
reasonable: local output may need parent directories; Azure needs its extension
loaded and appropriate authentication configured. These differences do **not**
justify implementing another upload client, separate format writers per service,
or mandatory staging/promotion as part of basic preparation.

Two registered preparation functions would be enough if registration is wanted.
Separate strategy classes are an implementation choice, not a DuckDB requirement.
Map `az`, `azure` and `abfss` to shared Azure connection preparation. Do not add
an ADLS directory-creation implementation solely because ADLS has directories:
the exact missing-parent service behavior remains unverified here, as detailed
below. A writable, provisioned destination is a sufficient starting contract.

**Preserving an existing Azure export when COPY fails is a separate requirement.**
The pinned extension truncates the final object during open. DuckDB disables its
temporary-file path for remote URIs. A stronger publication contract therefore
needs additional work; merely loading the extension, authenticating or creating
directories cannot provide it. Conversely, local single-file replacement already
uses DuckDB temporary output by default when the destination exists.

These conclusions come from upstream code, not the current Assquack wrappers.

## Version and evidence boundary

The [declared dependency](../../pyproject.toml) is `duckdb>=1.5.0,<2.0.0`.
[Poetry's lock](../../poetry.lock) and `poetry run python` both identify **1.5.2**.
An in-memory connection with automatic extension installation and loading disabled
reported JSON and Parquet installed/loaded at `v1.5.2`, and Azure neither installed
nor loaded. No Azure binary was downloaded or tested.

DuckDB's [v1.5.2 Azure build configuration, lines 1–7](https://github.com/duckdb/duckdb/blob/v1.5.2/.github/config/extensions/azure.cmake#L1-L7)
pins `duckdb-azure` to **`ea6ffae3710ec568ce08579dbfc0cddc8c759227`**.
That is the extension source used throughout this report. It is not proof of the
revision of a future deployment's downloaded binary.

Full source files were retrieved through the GitHub connector. Line links below
refer to those files, including blank lines. The earlier
[destination report](2026-10-23-export-destination-architecture.md),
[scope journal](../journal/2026-09-08-mvp-scope.md) and [exports doc](../exports.md)
provide context; they are not authority for upstream behavior. This report follows
the requested newer `FileSystemDestination(base_uri, auth)` discussion without
rewriting those shared documents.

## Shared execution, step by step

1. **Bind COPY.** `Binder::BindCopyTo` resolves the format, binds its options and
   creates a logical COPY operator. It detects remote paths and forces
   `use_tmp_file = false`; for an ordinary local file, the default is temporary
   output when that file already exists. Explicit local `USE_TMP_FILE true` can
   request staging for a new file as well.
   [Binder, lines 205–235 and 279–310](https://github.com/duckdb/duckdb/blob/v1.5.2/src/planner/binder/statement/bind_copy.cpp#L205-L310).
2. **Plan execution.** `PhysicalPlanGenerator::CreatePlan` expands the path and,
   when temporary output is enabled, substitutes the sibling name `tmp_<basename>`.
   It selects regular or batch COPY according to format capabilities, order and
   batch-index support. This is a deterministic temporary name, not a unique
   per-run staging identifier.
   [Planner, lines 8–80](https://github.com/duckdb/duckdb/blob/v1.5.2/src/execution/physical_plan/plan_copy_to_file.cpp#L8-L80).
3. **Initialize the format writer.** Parquet constructs a `BufferedFileWriter`
   with write/create-new flags. CSV does the same. In this API, create-new means
   create-or-truncate, not exclusive creation. JSON's `CopyToJSONPlan` projects
   rows through `struct_pack`/`to_json`, then binds the CSV writer with JSON framing.
   JSON array and newline-delimited output therefore share CSV's filesystem path.
   [Parquet constructor, lines 376–395](https://github.com/duckdb/duckdb/blob/v1.5.2/extension/parquet/parquet_writer.cpp#L376-L395),
   [CSV constructor, lines 64–77](https://github.com/duckdb/duckdb/blob/v1.5.2/src/common/csv_writer.cpp#L64-L77),
   [JSON plan, lines 20–120](https://github.com/duckdb/duckdb/blob/v1.5.2/extension/json/json_functions/copy_json.cpp#L20-L120).
4. **Open and write through DuckDB.** `BufferedFileWriter` calls `fs.OpenFile`,
   buffers data and calls `fs.Write`. `VirtualFileSystem` selects the filesystem
   for the path; subsequent writes dispatch through the returned handle. Assquack
   need not open a Python file or register a Python filesystem for native access.
   [Buffered writer, lines 14–79](https://github.com/duckdb/duckdb/blob/v1.5.2/src/common/serializer/buffered_file_writer.cpp#L14-L79),
   [VFS open/write, lines 115–190](https://github.com/duckdb/duckdb/blob/v1.5.2/src/common/virtual_file_system.cpp#L115-L190).
5. **Finalize and close.** Parquet writes its footer and closes the buffered writer;
   `WriteCSVFinalize` completes text framing and closes `CSVWriter`. Buffered close
   flushes pending bytes and invokes the filesystem handle's `Close()`.
   [Parquet finalization, lines 1113–1182](https://github.com/duckdb/duckdb/blob/v1.5.2/extension/parquet/parquet_writer.cpp#L1113-L1182),
   [CSV finalization, lines 371–382](https://github.com/duckdb/duckdb/blob/v1.5.2/src/function/table/copy_csv.cpp#L371-L382).
6. **Promote only if temporary output was selected.** Both regular
   `FinalizeInternal` and batch `FinalFlush` finalize the format before invoking
   `PhysicalCopyToFile::MoveTmpFile`. This invokes the filesystem's `MoveFile`.
   [Regular finalization, lines 632–668](https://github.com/duckdb/duckdb/blob/v1.5.2/src/execution/operator/persistent/physical_copy_to_file.cpp#L632-L668),
   [Batch finalization, lines 314–332](https://github.com/duckdb/duckdb/blob/v1.5.2/src/execution/operator/persistent/physical_batch_copy_to_file.cpp#L314-L332).

## Local and Azure comparison

| Stage | Local file | Azure Blob: `az://`, `azure://` | ADLS Gen2: `abfss://` |
| --- | --- | --- | --- |
| Basic setup | Existing writable parent, or create it | Load Azure extension; configure suitable secret; existing container | Same extension and secret mechanisms; existing filesystem/container; qualify directory assumptions below |
| Open | OS create/truncate at selected final or temporary name | Get properties, then commit empty block list for creation/overwrite | Get properties, then file `Create()` for creation/overwrite |
| Write | OS writes through DuckDB buffering | `StageBlock` per filesystem write | Sequential `Append` at current offset |
| Close | Flush buffered bytes, close OS handle | `Sync()` commits accumulated block IDs | `Sync(true)` calls `Flush` with close flag |
| Existing final target | Normally left intact while sibling temporary file is written | Truncated during open | Truncated during open |
| Automatic promotion | Local `MoveFile` after finalization | None | None |
| Failure | Cleanup depends on execution path and failure stage | No restoration of previous object | No restoration of previous file |

### Local directories and replacement

For a normal single-file export, `GetGlobalSinkState` initializes the writer
directly. Its directory-creation branch applies to partitioned, per-thread or
rotating output. It does not recursively create a single-file target's parent.
The OS open implementation uses `O_CREAT`/`O_TRUNC` on Unix or `CREATE_ALWAYS` on
Windows. Neither is recursive directory creation.
[COPY initialization, lines 446–487](https://github.com/duckdb/duckdb/blob/v1.5.2/src/execution/operator/persistent/physical_copy_to_file.cpp#L446-L487),
[Unix open, lines 315–350](https://github.com/duckdb/duckdb/blob/v1.5.2/src/common/local_file_system.cpp#L315-L350),
[Windows open, lines 1027–1085](https://github.com/duckdb/duckdb/blob/v1.5.2/src/common/local_file_system.cpp#L1027-L1085).

Therefore `Path(target).parent.mkdir(parents=True, exist_ok=True)` is a sufficient
Assquack operation **if Assquack promises automatic local parent creation**.
If parents are provisioned externally, no directory call is required. This is
path preparation, not authentication or a new filesystem implementation.

Local promotion calls Unix `rename`, or Windows `MoveFileExW` with
`MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH`. Do not extend this observation
into a blanket crash-durability, concurrent-writer or mounted-filesystem guarantee.
[Unix move, lines 795–803](https://github.com/duckdb/duckdb/blob/v1.5.2/src/common/local_file_system.cpp#L795-L803),
[Windows move, lines 1343–1350](https://github.com/duckdb/duckdb/blob/v1.5.2/src/common/local_file_system.cpp#L1343-L1350).

### Azure Blob

`AzureBlobStorageFileSystem::LoadRemoteFileInfo` calls `GetProperties`; the
creation/truncation operation is `CommitBlockList({})`. Writes stage blocks and
close commits their IDs. Block staging does not preserve the old object here,
because the empty commit already occurred. Container clients are obtained, not
created. Flat Blob prefixes do not require directory creation; the extension's
`CreateDirectory` override is empty.
[Blob open, lines 270–324](https://github.com/duckdb/duckdb-azure/blob/ea6ffae3710ec568ce08579dbfc0cddc8c759227/src/azure_blob_filesystem.cpp#L270-L324),
[write, lines 415–449](https://github.com/duckdb/duckdb-azure/blob/ea6ffae3710ec568ce08579dbfc0cddc8c759227/src/azure_blob_filesystem.cpp#L415-L449),
[close/sync, lines 80–104](https://github.com/duckdb/duckdb-azure/blob/ea6ffae3710ec568ce08579dbfc0cddc8c759227/src/azure_blob_filesystem.cpp#L80-L104),
[directory override, lines 54–58](https://github.com/duckdb/duckdb-azure/blob/ea6ffae3710ec568ce08579dbfc0cddc8c759227/src/include/azure_blob_filesystem.hpp#L54-L58).

Do not run Python `mkdir`, create directory-marker blobs, or manually stage and
commit blocks before COPY. Those are unnecessary or duplicate DuckDB's work.
An HNS-enabled account accessed through the Blob endpoint must not automatically
be assigned every flat-namespace assumption; endpoint and namespace both matter.

### ADLS: verified code and the missing-parent limit

`AzureDfsStorageFileSystem::LoadRemoteFileInfo` calls `GetProperties` followed by
`DataLakeFileClient::Create()` for a new/overwritten file. It contains no parent
walk. The separate `CreateDirectory` method calls the directory client's `Create()`;
it is not called by ordinary single-file initialization. Writes use `Append`;
sync/close use `Flush`.
[ADLS open, lines 280–326](https://github.com/duckdb/duckdb-azure/blob/ea6ffae3710ec568ce08579dbfc0cddc8c759227/src/azure_dfs_filesystem.cpp#L280-L326),
[directory creation, lines 159–168](https://github.com/duckdb/duckdb-azure/blob/ea6ffae3710ec568ce08579dbfc0cddc8c759227/src/azure_dfs_filesystem.cpp#L159-L168),
[write/sync, lines 358–388](https://github.com/duckdb/duckdb-azure/blob/ea6ffae3710ec568ce08579dbfc0cddc8c759227/src/azure_dfs_filesystem.cpp#L358-L388),
[close, lines 109–120](https://github.com/duckdb/duckdb-azure/blob/ea6ffae3710ec568ce08579dbfc0cddc8c759227/src/azure_dfs_filesystem.cpp#L109-L120).

The extension's [vcpkg baseline](https://github.com/duckdb/duckdb-azure/blob/ea6ffae3710ec568ce08579dbfc0cddc8c759227/vcpkg.json)
resolves the standalone manifest's Data Lake dependency to
[SDK 12.13.0](https://github.com/microsoft/vcpkg/blob/84bab45d415d22042bd0b9081aea57f362da3f35/ports/azure-storage-files-datalake-cpp/vcpkg.json).
Its [file `Create`, lines 133–138](https://github.com/Azure/azure-sdk-for-cpp/blob/azure-storage-files-datalake_12.13.0/sdk/storage/azure-storage-files-datalake/inc/azure/storage/files/datalake/datalake_file_client.hpp#L133-L138)
delegates to [path `Create`, lines 202–260](https://github.com/Azure/azure-sdk-for-cpp/blob/azure-storage-files-datalake_12.13.0/sdk/storage/azure-storage-files-datalake/src/datalake_path_client.cpp#L202-L260),
which constructs one protocol request, not a recursive client-side mkdir loop.
Combined extension builds may resolve dependencies differently; this is a source
dependency trace, not an installed SDK identification.

**This establishes absence of client-side parent preparation, not the service's
behavior for every missing ancestor.** The inspected official
[Path Create API](https://learn.microsoft.com/en-us/rest/api/storageservices/datalakestoragegen2/path/create)
does not establish a sufficiently explicit recursive-parent guarantee here.
Upstream [cloud write tests](https://github.com/duckdb/duckdb-azure/blob/ea6ffae3710ec568ce08579dbfc0cddc8c759227/test/sql/cloud/azure_writes.test)
exercise nested ADLS paths, but do not prove their parents were initially absent.
No live request was made. Accordingly:

- Preprovisioned writable ADLS paths require no additional Assquack mkdir call.
- Automatic creation of arbitrary missing ADLS ancestors is **not certified** by
  this research. Do not claim either guaranteed implicit creation or a mandatory
  new SDK mkdir implementation from the inspected client code alone.
- Resolve that exact service behavior before promising automatic ADLS hierarchy
  preparation. It is not a reason to delay shared COPY or invent a class now.

## Extension and authentication setup are separate from transport

At deployment, provision the compatible Azure extension. At runtime, ensure it
is loaded (`LOAD azure` when not already loaded) before using its secret provider
and filesystem. Loading registers both native Azure filesystem implementations;
Assquack does not register them itself.
[Extension registration, lines 9–16](https://github.com/duckdb/duckdb-azure/blob/ea6ffae3710ec568ce08579dbfc0cddc8c759227/src/azure_extension.cpp#L9-L16).
Runtime `INSTALL azure` is not an inherent per-export step.

For the proposed authentication contract, register a temporary, appropriately
scoped `TYPE azure` secret in the database instance used by the export connection:

- SAS: native `PROVIDER config` with a validated SAS connection string.
- Credential chain: `PROVIDER credential_chain`, with account/chain selection
  when required by the URI or deployment.

Both Blob and ADLS use these providers. Config secret creation stores the supplied
connection string; credential-chain secret creation stores the chain selection.
The client layer performs secret lookup and builds the appropriate SDK clients.
Assquack supplies configuration; it should not recreate native token discovery.
[Secret factories, lines 35–83](https://github.com/duckdb/duckdb-azure/blob/ea6ffae3710ec568ce08579dbfc0cddc8c759227/src/azure_secret.cpp#L35-L83),
[client construction and lookup, lines 411–458 and 697–734](https://github.com/duckdb/duckdb-azure/blob/ea6ffae3710ec568ce08579dbfc0cddc8c759227/src/azure_storage_account_client.cpp#L411-L734).
Secret registration is configuration, not proof that credentials have succeeded
against storage. No secrets, environment credentials or identity providers were
accessed in this research.

## Failure cleanup and stronger publication

Regular COPY's `CopyToFunctionGlobalState` destructor attempts `TryRemoveFile` for
recorded outputs only when initialized and not finalized, swallowing cleanup
exceptions. Initialization failures can precede that guard. Azure implementations
map removal to `DeleteIfExists`; deleting a damaged replacement does not restore
the previous object.
[Regular cleanup, lines 260–274](https://github.com/duckdb/duckdb/blob/v1.5.2/src/execution/operator/persistent/physical_copy_to_file.cpp#L260-L274),
[Blob removal, lines 376–383](https://github.com/duckdb/duckdb-azure/blob/ea6ffae3710ec568ce08579dbfc0cddc8c759227/src/azure_blob_filesystem.cpp#L376-L383),
[ADLS removal, lines 187–193](https://github.com/duckdb/duckdb-azure/blob/ea6ffae3710ec568ce08579dbfc0cddc8c759227/src/azure_dfs_filesystem.cpp#L187-L193).

Batch COPY's `FixedBatchCopyGlobalState` has no corresponding created-files
cleanup destructor. The regular operator's cleanup must not be generalized to
all COPY plans. A crash, failed close, failed removal or failed promotion also
exceeds a simple successful-finalization guarantee.
[Batch state, lines 66–147](https://github.com/duckdb/duckdb/blob/v1.5.2/src/execution/operator/persistent/physical_batch_copy_to_file.cpp#L66-L147).

Neither Azure subclass supplies `MoveFile`; the base implementation throws
`NotImplementedException`. Thus ADLS service rename capability does not imply
that this DuckDB extension exposes promotion through its filesystem interface.
[Azure Blob class](https://github.com/duckdb/duckdb-azure/blob/ea6ffae3710ec568ce08579dbfc0cddc8c759227/src/include/azure_blob_filesystem.hpp),
[Azure DFS class](https://github.com/duckdb/duckdb-azure/blob/ea6ffae3710ec568ce08579dbfc0cddc8c759227/src/include/azure_dfs_filesystem.hpp),
[base MoveFile, lines 579–581](https://github.com/duckdb/duckdb/blob/v1.5.2/src/common/file_system.cpp#L579-L581).

If Assquack promises preservation during failed export, it must define staging,
promotion, completion verification and cleanup separately. Unique staging names
and an appropriate publication operation are possible additions; merely writing
to a different URI does not complete publication. ADLS rename and flat Blob
publication require different qualification. This report does not authorize a
provider SDK dependency or claim a verified atomic promotion implementation.

Local staging beyond DuckDB's default can add unique names, new-target visibility
control or explicit ownership of cleanup. It is optional for basic writing.
No single-file mechanism makes several formats/destinations and a DuckDB manifest
one atomic transaction.

## Recommended minimum boundary

| Owner | Minimum responsibility |
| --- | --- |
| `FileSystemDestination` | Base URI, authentication choice and destination-level orchestration; retain broader `ExportDestination` possibility |
| Shared DuckDB writer | Produce and execute format-appropriate COPY against the selected target; propagate success/failure |
| One DuckDB filesystem adapter | Coordinate native capability/configuration preparation; allow composed handlers without provider branches in the writer |
| Local preparation handler, if needed | Ensure parent exists when automatic directory creation is part of the contract |
| Shared Azure preparation handler | Ensure extension is loaded; configure selected authentication; require a supported writable destination |
| DuckDB internals | Select native filesystem, open, encode, buffer, upload/write, finalize and close |
| Separate publication policy, if promised | Preserve old output, stage/publish/clean up with explicitly supported backend semantics |

This is an inference from the evidence, not a required class hierarchy. A small
scheme-to-function mapping can express all currently verified setup differences.
Authentication strategies may be composed inside Azure preparation; there is no
evidence that basic preparation needs an Azure upload strategy. No fsspec
implementation, dependency or Python filesystem registration is required.

## Validation and remaining limits

Offline synthetic checks used installed DuckDB 1.5.2 on Windows, one thread,
automatic extension installation/loading disabled, and files under this chat's
scratch directory outside the repository. For each of Parquet, CSV and JSON:

- COPY to a missing nested local parent failed.
- Creating the parent allowed COPY to succeed.
- A subsequent COPY raising `error('synthetic failure')` on row 9,999 preserved
  the old output bytes; no sibling temporary file remained in that experiment.

These checks corroborate this execution path; they do not establish every plan,
concurrent-writer, crash or mount behavior. An initial system-temp attempt hit
sandbox permissions and was rerun in the explicitly writable scratch directory.
No live Azure, provider calls, extension installation, credentials or customer
systems were used. Azure results are source findings, not cloud validation.

Remaining uncertainties are the actual deployed Azure binary/dependency versions,
ADLS missing-ancestor service behavior, and any stronger publication protocol.
Those should remain explicit acceptance questions rather than invented setup
requirements. The broad package constraint permits versions not traced here.

Only this dedicated report is changed by this research. The documentation index
is deliberately untouched under the request's scope restriction.

Documentation validation: the repository Markdown link/anchor checker passed;
the report's terminology scan found no prohibited terms; whitespace checks passed.
Application tests, lint and type checks were not run for this research-only change.

## Related Docs

- [Prior export destination research](2026-10-23-export-destination-architecture.md):
  wider backend comparison, independently checked here for local/Azure.
- [Exports](../exports.md): current public behavior and publication limitations.
- [Configuration](../configuration.md): existing Azure authentication contract.
- [MVP scope journal](../journal/2026-09-08-mvp-scope.md): decision history.
