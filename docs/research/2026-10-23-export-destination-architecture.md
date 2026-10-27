# Export serialization and destination architecture

Research date: **2026-10-23**, Australia/Sydney. Research only; not an approved
expansion of MVP scope or an implementation specification.

## Verdict

**Yes: Assquack can share one DuckDB `COPY` execution path, with format-specific
options for Parquet, CSV and JSON/NDJSON, across all five destination categories.**
The storage provider does not require a different Parquet, CSV or JSON encoding.
Native filesystem implementations handle transport. However, destination
differences cannot be confined to authentication: URI validation, extension
provisioning, upload constraints and especially safe replacement belong to the
destination boundary too. R2 compatibility and particular extension builds must
be qualified rather than promised universally.

**The immediate issue is Azure publication safety.** The Azure source pinned by
DuckDB 1.5.2 truncates an existing target when opened for overwrite. DuckDB 1.5.2
disables its temporary-file path for remote URIs. Sharing serialization does not
solve preservation of the previous successful export.

## Evidence and version boundary

Labels used below:

- **Source-confirmed:** repository code, version-pinned upstream source or official
  documentation; this is not a successful cloud test.
- **Tested:** the narrowly described offline experiment in this report.
- **Inference/recommendation:** architecture derived from that evidence.

The inspected checkout was at `b0ba1c85e2204036e296b6af6e2550ea09303b0b`, with
concurrent uncommitted work. Observations are a point-in-time reading, not changes
made by this research. MAD.Prefect reference HEAD was
`59a811ff4003eb68ba77f8fdeb9cbdabf09b2f19`.

The [package constraint](../../pyproject.toml) is `duckdb>=1.5.0,<2.0.0`;
[Poetry's lock](../../poetry.lock) selects **1.5.2**, and the Poetry interpreter
reported **1.5.2**. Offline `duckdb_extensions()` inspection found:

| Extension | Installed / loaded | Reported version |
| --- | --- | --- |
| json | yes / yes | v1.5.2 |
| parquet | yes / yes | v1.5.2 |
| azure | no / no | none |
| httpfs | no / no | none |
| aws | no / no | none |

No cloud extension was installed or loaded during this assessment. There is
therefore **no installed Azure/HTTPFS build to certify**. DuckDB's v1.5.2 release
configuration pins these source revisions:

| Extension | v1.5.2 build-configuration revision |
| --- | --- |
| azure | `ea6ffae3710ec568ce08579dbfc0cddc8c759227` |
| httpfs | `13e18b3c9f3810334f5972b76a3acc247b28e537` |
| aws | `b2649e68341a9ee717588dd23f277904727ce793` |

Sources: release [Azure configuration](https://github.com/duckdb/duckdb/blob/v1.5.2/.github/config/extensions/azure.cmake),
[HTTPFS configuration](https://github.com/duckdb/duckdb/blob/v1.5.2/.github/config/extensions/httpfs.cmake),
and [AWS configuration](https://github.com/duckdb/duckdb/blob/v1.5.2/.github/config/extensions/aws.cmake).
These pins identify the source inspected; they do not prove that a subsequently
downloaded extension has the same revision. Deployment qualification must record
the actual engine, platform, extension version and installation source. Avoid
using `stable`/`current` documentation uncritically: those URLs now redirect to
2.0, outside Assquack's allowed engine range. Azure writes were introduced in
[DuckDB 1.5.0](https://duckdb.org/2026/03/09/announcing-duckdb-150#azure-writes).

## Format-by-destination matrix

`Native` means the native filesystem and COPY architecture supports this path;
it does not mean this assessment ran it against a service. JSON support is
particularly well grounded: the [1.5.2 JSON COPY implementation](https://github.com/duckdb/duckdb/blob/v1.5.2/extension/json/json_functions/copy_json.cpp)
builds each row with `struct_pack`/`to_json`, then uses the CSV writer with JSON
framing options. It inherits that writer's filesystem path.

| Destination | Parquet | CSV | JSON / NDJSON | Destination requirements | Evidence strength |
| --- | --- | --- | --- | --- | --- |
| Local disk | Native; tested | Native; tested | Native; tested | Directory, permissions, available space | Offline 1.5.2 experiment |
| Mounted storage | Same native path | Same native path | Same native path | Mount semantics and credentials managed outside COPY | Architecture inference; mount untested |
| Azure Blob | Native | Native | Native via common writer | `azure`; `az://` or `azure://`; Azure secret | 1.5 docs and pinned source |
| ADLS Gen2 | Native | Native | Native via common writer | `azure`; `abfss://`; Azure secret | 1.5 docs and pinned source |
| Amazon S3 | Native | Native | Native via common writer | `httpfs`; `s3://`; S3 secret; `aws` for credential chain | DuckDB names AWS as tested |
| Google Cloud Storage | Native via XML/S3 interoperability | Same | Same | `httpfs`; `gs://` or `gcs://`; HMAC | DuckDB names Google Cloud as tested |
| MinIO | Native via S3 API | Same | Same | `httpfs`; `s3://`; endpoint, addressing, credentials | DuckDB names MinIO as tested |
| Cloudflare R2 | Native compatibility path, qualification required | Same transport condition | Same transport condition | `httpfs`; `r2://` or configured `s3://`; R2 credentials | Official support guidance; not in DuckDB's tested-platform list |

DuckDB's [1.5 S3 support page](https://duckdb.org/docs/1.5/core_extensions/httpfs/s3api)
explicitly distinguishes tested AWS/MinIO/Google Cloud/lakeFS from other compatible
services. It documents multipart writes for CSV and Parquet. Azure's
[1.5 extension page](https://duckdb.org/docs/1.5/core_extensions/azure) documents
both Blob and ADLS writes. No row requires provider-specific serialization.

## Keep five responsibilities distinct

1. **Serialization:** project table columns into the intended consumer schema;
   select Parquet, CSV or NDJSON and validated format options.
2. **URI resolution:** expand pipes and `mad://`, join the configured base, and
   validate the resulting destination. A prefix is an output location, separate
   from a credential or identity selector.
3. **Authentication:** obtain runtime identity or explicit credentials and
   register a temporary, correctly scoped secret on the export connection.
4. **Transport:** DuckDB's local, Azure or HTTPFS filesystem moves bytes, handling
   service requests, buffering and multipart/block operations.
5. **Publication:** determine when consumers may observe the replacement,
   preserve prior output on failure, and record success only after publication.

This is a division of responsibilities, not a recommendation for five registries
or class hierarchies. A format-options mapping, one COPY helper and a small
destination lifecycle are sufficient starting points.

## Destination findings

### Local filesystem and mounts

**Tested:** all three formats wrote and read a synthetic row, replaced it, and
preserved the existing bytes when a subsequent COPY failed on a conversion.
Explicit `USE_TMP_FILE true` also worked for each format.

**Source-confirmed:** the [1.5.2 binder](https://github.com/duckdb/duckdb/blob/v1.5.2/src/planner/binder/statement/bind_copy.cpp)
normally enables temporary output for an existing local single-file target.
It unconditionally disables this path for recognized remote URIs, even if the
option was supplied. The [physical COPY operator](https://github.com/duckdb/duckdb/blob/v1.5.2/src/execution/operator/persistent/physical_copy_to_file.cpp)
uses filesystem `MoveFile` for promotion and attempts cleanup after failures.
These mechanisms are not a multi-output transaction or a power-loss guarantee.

**Recommendation:** use a unique sibling staging file and a verified same-volume
replacement operation for explicit Assquack publication. Do not stage on a
different disk and assume rename remains atomic. Windows sharing modes and open
handles can prevent replacement.

A local path can mean a real disk, network filesystem such as SMB/NFS, or an
object-storage FUSE mount. DuckDB sees the path rather than the backing service.
Formatting remains identical, but locking, close visibility, caches, upload
completion, rename and durability follow the mount. For example,
[Cloud Storage FUSE semantics](https://github.com/GoogleCloudPlatform/gcsfuse/blob/master/docs/semantics.md)
distinguish hierarchical-namespace directory renames from flat-bucket behavior.
Do not advertise every mounted path as having local-disk publication guarantees.
The current `file://` conversion also needs qualification for UNC authorities;
the inspected code uses the parsed path and does not preserve the URI authority.

### Azure Blob and ADLS Gen2

**Source-confirmed:** Blob accepts `az://container/path` / `azure://container/path`
with account configuration, or an account-qualified Blob endpoint. ADLS uses
`abfss://account.dfs.core.windows.net/filesystem/path` or the short filesystem
form with account configuration. Do not pass Hadoop's
`abfss://container@account.dfs.core.windows.net/path` unchanged to Assquack's
current validator. Azure secrets support native identity discovery and explicit
configuration; the repository implements credential-chain and SAS selection.
See [DuckDB 1.5 Azure usage/authentication](https://duckdb.org/docs/1.5/core_extensions/azure).

The version-pinned [Blob implementation](https://github.com/duckdb/duckdb-azure/blob/ea6ffae3710ec568ce08579dbfc0cddc8c759227/src/azure_blob_filesystem.cpp)
calls `StageBlock` for each filesystem write and `CommitBlockList` on sync/close.
Its overwrite-open path first commits an **empty block list**, truncating the
old blob. The pinned [ADLS implementation](https://github.com/duckdb/duckdb-azure/blob/ea6ffae3710ec568ce08579dbfc0cddc8c759227/src/azure_dfs_filesystem.cpp)
creates/truncates the file, sends sequential `Append` requests and calls `Flush`
on sync/close. Neither is a format-specific implementation. Both require staging
away from the final name if preservation of the old artifact is required.

Blob has a 50,000 committed-block ceiling; modern service versions allow blocks
up to 4,000 MiB. With the pinned implementation's one-write/one-block behavior,
many small writer flushes can exhaust the block count well before the service's
maximum object size. Writer buffering can therefore affect practical limits even
though file encoding does not change. [Azure Put Block List limits](https://learn.microsoft.com/en-us/rest/api/storageservices/put-block-list).
Current Azure extension source adds buffering controls absent from the inspected
release source; do not assume current tuning options exist in the deployment.

ADLS hierarchical namespace supplies a real rename operation. The
[Path Create/rename API](https://learn.microsoft.com/en-us/rest/api/storageservices/datalakestoragegen2/path/create)
supports overwrite and conditional requests, with lease and encryption-policy
constraints. Microsoft describes HNS rename/move as atomic in its
[Data Lake SDK documentation](https://github.com/MicrosoftDocs/azure-docs-sdk-python/blob/main/docs-ref-services/latest/storage-file-datalake-readme.md).
Flat Blob rename is copy/delete, as Microsoft's
[namespace comparison](https://learn.microsoft.com/en-us/azure/storage/blobs/upgrade-to-data-lake-storage-gen2)
explains. These service capabilities do not mean COPY automatically performs
staging/promotion. A publication adapter may need an approved provider API;
that is additional transport/publication work, not another serializer.

### Amazon S3

**Source-confirmed:** native HTTPFS multipart upload is available, with configured
key/session credentials or the AWS extension's credential-chain provider.
Select the bucket region, endpoint when needed, TLS and addressing in destination
setup. The [pinned HTTPFS uploader](https://github.com/duckdb/duckdb-httpfs/blob/13e18b3c9f3810334f5972b76a3acc247b28e537/src/s3fs.cpp)
calculates a fixed part size from maximum file size/part count, bounded below by
5 MiB and rounded to buffer blocks. It fills those buffers before upload.
Memory grows with active parts and concurrent files; serialization also needs
its own row-group/text buffers. “Streaming” does not imply negligible memory.

S3's object update is atomic at one key, not across keys.
[AWS consistency guarantees](https://docs.aws.amazon.com/AmazonS3/latest/userguide/Welcome.html).
Multipart parts are assembled at completion and can persist, incur charges and
need abort/lifecycle cleanup after interruption.
[AWS multipart lifecycle](https://docs.aws.amazon.com/AmazonS3/latest/userguide/mpuoverview.html).
Do not infer Assquack's end-to-end failure contract solely from that atomicity:
client cleanup, cancellation, completion-response loss and later metadata failure
remain separate cases.

Current service limits are 10,000 parts, 5 MiB–5 GiB per nonfinal part, and 48.8 TiB
maximum object size. These are not DuckDB 1.5.2's practical limits or the common
denominator for other S3 implementations. [AWS limits](https://docs.aws.amazon.com/AmazonS3/latest/userguide/qfacts.html).
General-purpose-bucket rename is copy then delete, not local rename.
[AWS copy/move guidance](https://docs.aws.amazon.com/AmazonS3/latest/userguide/copy-object.html).
Specialized bucket APIs must be qualified independently rather than generalized
to every `s3://` target.

### Google Cloud Storage

**Source-confirmed:** DuckDB uses GCS's S3 interoperability path, with `TYPE gcs`
and **HMAC** credentials. Ordinary service-account JSON keys, ADC or OAuth tokens
are not interchangeable with these HMAC fields. The
[DuckDB export guide](https://www.duckdb.org/docs/preview/guides/network_cloud_storage/s3_export)
shows native `gs://` exports; the 1.5 S3 documentation independently confirms
the GCS secret/URI route. This is the XML multipart API, not the unrelated
JSON API upload method with the same “multipart” name.
[Google XML multipart documentation](https://docs.cloud.google.com/storage/docs/multipart-uploads).

The service permits 5 TiB objects, 10,000 parts and 5 MiB–5 GiB nonfinal parts.
Unfinished multipart uploads can remain indefinitely; plan lifecycle cleanup.
[Google quotas](https://docs.cloud.google.com/storage/quotas).
Single-object replacements are atomic; batches are not.
[Google consistency documentation](https://docs.cloud.google.com/storage/docs/consistency).

Publication needs API-aware qualification: Google now documents an atomic
`Objects: move` operation, while some tools simulate move through copy/delete;
HNS folders also support atomic metadata renaming. Neither capability is an
automatic consequence of DuckDB's XML/S3 write path.
[Object move guidance](https://docs.cloud.google.com/storage/docs/copying-renaming-moving-objects),
[HNS folder semantics](https://docs.cloud.google.com/storage/docs/folders-overview).

**Inference:** if a deployment prohibits HMAC and requires ADC/OAuth, this native
1.5.2 route is insufficient as assessed. An explicit future alternate transport
could upload a locally COPY-generated file through a Google client. Serialization
would still be shared. This is a potential scope decision, not permission to add
a silent fsspec fallback.

### MinIO and Cloudflare R2

MinIO is on DuckDB's tested-platform list. Configure an S3 secret with the actual
MinIO endpoint and usually path-style addressing; the vendor's
[DuckDB integration guide](https://www.min.io/blog/duckdb-and-minio-for-a-modern-data-stack)
emphasizes path style. Use TLS for a TLS deployment. Server version, policy,
object limits and deployment configuration still need qualification; this report
does not claim testing every MinIO release or a universal maximum object size.
The shared HTTPFS uploader handles bytes. Treat generic rename as copy/delete
unless a particular service operation proves stronger semantics.

R2 has a documented native compatibility route through `TYPE r2` with account ID
and S3 credentials, or `TYPE s3` with its account-specific endpoint.
[DuckDB 1.5 platform-specific secrets](https://duckdb.org/docs/1.5/core_extensions/httpfs/s3api#platform-specific-secret-types),
[Cloudflare API surfaces](https://developers.cloudflare.com/r2/api/).
Its multipart rules require all nonfinal parts to have **equal size**, unlike
the general S3 minimum/maximum rule. It allows 10,000 parts; unfinished uploads
are aborted after seven days by default.
[R2 upload rules](https://developers.cloudflare.com/r2/objects/upload-objects/).
The detailed limits page gives a practical maximum of 4.995 TiB and one write per
second to the same key. [R2 limits](https://developers.cloudflare.com/r2/platform/limits/).

The pinned 1.5.2 uploader's fixed part sizing is consistent with that requirement
by source inspection, **not an R2 test**. An upstream
[HTTPFS issue #405](https://github.com/duckdb/duckdb-httpfs/issues/405) reports
unequal-part failure on 2.0 previews and success on 1.5.5. This is a reporter's
observation, not our test or proof about 1.5.2. It demonstrates why compatibility
must include uploader version and files large enough to exercise multipart.
Changing Parquet encoding is not the remedy for a multipart protocol mismatch.
R2 commits object metadata after writing data, but this does not supply a
multi-key transaction or generic rename. [R2 write architecture](https://developers.cloudflare.com/r2/how-r2-works/).

## Concrete shared COPY and setup examples

These examples show structure only. No cloud SQL below was executed. Provision
extensions during deployment; Assquack's runtime currently loads rather than
installs them. Runtime credential values should be bound parameters, never
embedded in source, logs or manifest options.

```sql
-- The execution shape is identical; substitute a validated destination URI.
COPY (SELECT * FROM bronze.orders_current)
TO 'exports/orders.parquet' (FORMAT parquet);

COPY (SELECT * FROM bronze.orders_current)
TO 'exports/orders.csv' (FORMAT csv, HEADER true);

COPY (SELECT * FROM bronze.orders_current)
TO 'exports/orders.json' (FORMAT json, ARRAY false);
```

`ARRAY false` means one object per line, including when the suffix is `.json`.
Changing to one JSON array is a consumer-format decision, not a storage-provider
requirement. CSV delimiter, null representation, date formatting, nested-value
projection and Parquet compression/schema likewise belong to format policy.
Parquet compression is internal to the format; it is distinct from wrapping the
entire output stream in gzip. Do not infer every compression option works from
successful uncompressed text writes.

```sql
-- Azure identity discovery; temporary secret on the COPY connection.
LOAD azure;
CREATE TEMPORARY SECRET export_azure (
    TYPE azure, PROVIDER credential_chain,
    ACCOUNT_NAME 'exampleaccount',
    SCOPE 'abfss://exampleaccount.dfs.core.windows.net/lake/exports/'
);
-- COPY target: abfss://exampleaccount.dfs.core.windows.net/lake/exports/orders.json

-- AWS native credential discovery, when deployed and authorized.
LOAD httpfs;
LOAD aws;
CREATE TEMPORARY SECRET export_s3 (
    TYPE s3, PROVIDER credential_chain, REGION 'ap-southeast-2',
    SCOPE 's3://example-bucket/exports/'
);
-- COPY target: s3://example-bucket/exports/orders.csv
```

For explicit credentials the application can bind placeholders through the same
connection, as the existing Azure implementation does:

```sql
CREATE TEMPORARY SECRET export_gcs (
    TYPE gcs, KEY_ID $key_id, SECRET $secret,
    SCOPE 'gs://example-bucket/exports/'
);
CREATE TEMPORARY SECRET export_minio (
    TYPE s3, KEY_ID $key_id, SECRET $secret,
    ENDPOINT 'minio.example.test:9000', URL_STYLE 'path', USE_SSL true,
    SCOPE 's3://example-bucket/exports/'
);
CREATE TEMPORARY SECRET export_r2 (
    TYPE r2, KEY_ID $key_id, SECRET $secret, ACCOUNT_ID $account_id,
    SCOPE 'r2://example-bucket/exports/'
);
```

Use the corresponding `gs://`, `s3://` or `r2://` target in the shared COPY helper.
Drop secrets and restore temporary settings after use, including error paths.
Never reinterpret an authentication failure as permission to try unrelated
credentials. The examples do not establish support for private endpoints,
sovereign clouds, proxies, encryption-policy variants or temporary-credential
refresh during a long export; those need deployment-specific checks.

## Safe publication and replacement

**Recommendation:** make destination preparation return a write URI and an
explicit publication action. COPY writes to that URI using format options.
Only successful publication produces a success manifest. The action may be a
no-op only where direct final-key replacement has been qualified against the
required failure contract.

| Destination | Candidate publication mechanism | Important limit |
| --- | --- | --- |
| Local disk | Unique sibling stage, then same-volume replace | Not cross-volume; verify OS/mount semantics |
| Azure Blob | Stage unique blob, provider-specific promotion | Flat namespace lacks rename; copy completion/conditions matter |
| ADLS HNS | Stage unique file, then conditional rename | COPY does not invoke the rename API automatically |
| S3 / MinIO / R2 | Qualified complete-object replacement, or staged object plus provider copy | Generic copy/delete is not an atomic move; multipart/orphans need handling |
| GCS | Qualified object replacement or API-specific atomic move | Native XML COPY does not automatically use JSON move/HNS rename |

An alternative is unique run-scoped output plus a current pointer/manifest. That
avoids overwriting previous bytes but changes the fixed-path consumer contract
and needs agreement; it is not silently adopted here. A local DuckDB manifest
does not make external readers of a fixed object path obey that pointer.

The publication contract must separate: failure before upload, failure mid-write,
failure during promotion, a successful remote commit whose response was lost,
and failure recording metadata after publication. A failed command can have an
ambiguous external outcome. Retrying safely may need object versions, ETags or
generation preconditions, and cleanup must never delete a newer writer's output.
Three pipe outputs are three publications; no provider makes those three files
and a local metadata transaction atomic together. Preserve truthful per-output
results rather than imply all-or-nothing publication.

Do not solve promotion by reading staged data and running COPY again to the final
path: that reserializes data, adds cost and reintroduces the unsafe write window.
Provider-side byte copy or rename is a separate operation. If the native-only
boundary cannot supply the required safe publication API, document that precise
gap and commission the smallest explicit adapter or dependency decision.

## Current Assquack and MAD.Prefect comparison

The inspected [writer implementation](../../src/assquack/_exports/parquet.py)
already has a common `DuckDBExportWriter._copy` and subclasses whose differences
are options: Parquet, CSV with a header, and JSON with `ARRAY false`.
[Selection](../../src/assquack/_exports/writers.py) maps the three names to these
classes. Destination routing still lives in that writer and permits local/Azure
only. [Azure setup](../../src/assquack/_exports/azure.py) registers temporary
credentials around COPY. Thus this research supports extracting destination
lifecycle responsibility, not creating fifteen writer implementations.

MAD.Prefect's [DataArtifact](../../.submodules/MAD.Prefect/mad_prefect/data_assets/data_artifact.py)
uses `jsonlines.Writer` plus its encoder for JSON, Arrow's `ParquetWriter` for
Parquet, and Arrow CSV writing for CSV. All obtain a binary handle through the
same filesystem wrapper. DuckDB relations already take a COPY route; a comment
describes bypassing the wrapped `mad` filesystem to address overwrite behavior.
The [filesystem wrapper](../../.submodules/MAD.Prefect/mad_prefect/filesystems.py)
uses `fsspec.core.url_to_fs(basepath, **storage_options)` and delegates open,
put and move to the resulting backend. Its generic `move` does not establish
atomic rename across providers. Its temporary local upload helper is not the
same as remote stage-and-promote.

The useful principle transfers: separate serialization from filesystem choice.
Assquack can simplify further because it exports an already materialized DuckDB
table, rather than independently serialize heterogeneous Python batches.
Behavioral migration tests still matter: Arrow versus DuckDB can differ in nulls,
timestamps, nested CSV strings, empty structures and Parquet logical types.
Those are consumer compatibility questions, not destination-specific writers.

fsspec would add backend coverage and Python authentication integrations, but also
dependencies and backend-specific buffering/move semantics. It does not give
universal atomic replacement. The approved [scope journal](../journal/2026-09-08-mvp-scope.md)
requires local/Azure, all three formats, native DuckDB and no fsspec fallback;
S3, GCS and compatible stores remain extension research, not newly approved MVP
features. Export support says nothing about writing a mutable DuckDB database
remotely: the canonical database remains local under the approved storage model.

## Minimum architecture and qualification work

1. Retain one parameterized COPY executor and a small validated format-options
   mapping. Preserve a writer extension boundary only where new formats need it.
2. Resolve aliases and expand pipe targets before writing. Each target carries its
   actual format, URI, role and options.
3. Give destinations ownership of URI validation, connection/auth setup,
   transport settings, staging, publication and cleanup. Keep credential sourcing
   independent of authentication strategy, as already intended for Azure.
4. Keep native local/Azure in the MVP. Add other destinations when commissioned,
   reusing the serializer. Reject unsupported setups explicitly.
5. Treat safe Azure replacement as unfinished release work. Do not mark it solved
   because ordinary COPY succeeds or because extraction failure skips export.

Before claiming a cloud destination supported, qualify the exact deployed engine
and extension build with synthetic authorized storage: every format, empty data,
representative nested/VARIANT values, overwrite, sufficiently large multipart
output, cancellation, denied permissions, expired credentials, lost completion
response, cleanup and failed manifest recording. Include concurrent writers if
they are part of the supported contract. Test mounted storage separately.
JSON `PARTITION_BY` is explicitly unsupported in the inspected 1.5.2 JSON planner;
do not assume every COPY option is universally available just because the
execution shape is shared.

## Checks and limitations

Offline Python used an in-memory DuckDB connection with extension auto-install
and autoload disabled. For each format it wrote one row containing an integer,
string and list, read the row count, replaced the output with `id=2`, attempted
a 10,000-row export containing a deliberately invalid integer conversion, compared
the preexisting bytes, and then exercised explicit temporary-file mode. All
three format sequences passed. This is a small exception-path check, not a
crash/power-loss, network, multipart or mount test. Temporary workspace files
were removed. An initial attempt failed on a reserved SQL alias before writing;
its system-temp directory cleanup was denied, so the corrected experiment used
an isolated workspace directory instead.

Only this new research document is authored by this chat. No application code,
shared indexes, scope files, branches or commits were changed. No credentials,
environment files or customer systems were accessed. Cloud behavior is source
evidence and explicitly identified inference, not live verification.

Documentation validation: the repository local-link/anchor checker passed;
`git diff --check` passed; a separate `git diff --no-index --check` against
`NUL` checked this new untracked document. The prescribed terminology scan found
only two existing references in the historical cache/locking observation and its
phase note, none in this report. No application test suite was run for this
docs-only assessment.

## Related Docs

- [Exports](../exports.md): active behavior and consumer contract.
- [Scope journal](../journal/2026-09-08-mvp-scope.md): approved MVP boundary.
- [Extensibility defects](../epics/01-mvp/06-extensibility-defects.md): EXT-03,
  EXT-04 and REL-01 are the relevant implementation follow-ups.
- [Storage model](../storage-model.md): canonical database boundary.
- [MAD.Prefect reference](../mad-prefect-reference.md): migration context.

The shared documentation index is intentionally unchanged while other chats are
active; the coordinating chat can integrate this link later.
