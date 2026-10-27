# Future fsspec writer: export composition stress test

Research date: **2026-10-23**, Australia/Sydney. Research and design recommendations
only. No application implementation or expansion of MVP scope.

## Verdict

**Yes. Keep `ExportDestination(base_uri, writer, auth)` and the shared
prepare → write → publish → cleanup flow.** One future fsspec-backed writer can
serialize committed DuckDB data to either local or Azure storage. It should not
need `LocalFsspecWriter` and `AzureFsspecWriter` subclasses.

The necessary qualification is that destination preparation is **typed for the
selected writer**. Native DuckDB needs a configured connection and write URI;
an Arrow/Python serializer needs a binary output handle. These are different
resources, even though destination identity and publication policy can be reused.
Do not force both into one object with unrelated optional fields.

Recommended small refinement: a registered preparation factory for each supported
`(writer, destination kind)` pair, composed into `LocalDestination` or
`AzureDestination`. Keep the existing authentication key
`(writer, destination kind, auth)`. Pair registration is a proposed internal
addition, not a change to the agreed public three-argument configuration.
Factories can initially live in each destination's registry; a separate public
plugin API is unnecessary for this research conclusion.

**MVP remains native DuckDB only**, local/Azure, local no-auth and Azure SAS/native
credential discovery. Neither fsspec nor adlfs nor Arrow is a proposed new MVP
dependency. The future writer below is a design test, not a delivery commitment.

## Evidence and scope

- **Repository-confirmed:** inspected source in this checkout, including existing
  concurrent edits. HEAD observed during research:
  `8c028d1a3f7b3a34ff02390a97982ef2ab87f013`; MAD.Prefect reference:
  `59a811ff4003eb68ba77f8fdeb9cbdabf09b2f19`.
- **Upstream-confirmed:** official documentation or source, accessed on the
  research date. This is not a storage integration test. adlfs `main` and current
  fsspec/Arrow documentation are moving references, not a qualified release set.
- **Recommendation:** deductions and proposed contracts; not approved code.
- **Untested:** all proposed Python/fsspec/cloud execution paths in this report.

The latest human direction in the parent chat supersedes the older journal
pseudocode: URI selection belongs to registered destination behavior, not a
backend conditional inside the shared writer. The journal already records the
public configuration, writer/auth factories and format argument. This report
does not edit those shared records or choose unresolved public defaults.

## What the existing code proves

**Repository-confirmed:** [Assquack's writer](../../src/assquack/_exports/parquet.py)
has a common native `COPY` helper but still branches on local/Azure itself.
[Writer selection](../../src/assquack/_exports/writers.py) is currently keyed by
format; the proposed writer-name registry is not implemented there.
[Azure setup](../../src/assquack/_exports/azure.py) installs a temporary secret on
the actual export connection and removes it. SAS sourcing and authentication
selection remain separate concepts despite hardcoded selection today.

The MAD.Prefect [filesystem wrapper](../../.submodules/MAD.Prefect/mad_prefect/filesystems.py)
calls `url_to_fs(basepath, **storage_options)` and returns backend file handles.
Its [DataArtifact](../../.submodules/MAD.Prefect/mad_prefect/data_assets/data_artifact.py)
uses those handles with Arrow Parquet/CSV or jsonlines serialization. DuckDB
relations take a different route: register the wrapped filesystem and execute
`COPY`. Its comment describes an overwrite workaround. The wrapper's generic
`move` and `async def` declarations do not establish atomic publication or
nonblocking serialization. These are useful boundaries to learn from, not code
to reproduce wholesale.

## How committed data becomes bytes

fsspec supplies filesystem operations and byte streams. **It does not turn a
DuckDB table into Parquet, CSV or NDJSON.** A future writer needs an explicit
serialization implementation. Three routes are possible:

| Route | Data path | Implications |
| --- | --- | --- |
| Handle-based writer, assessed below | Committed DuckDB query → bounded Arrow batches → serializer → fsspec binary handle | No full output staging file required; extra conversion/serializer dependencies and compatibility work |
| DuckDB serialization plus upload | Native `COPY` to a local temporary file → fsspec upload | Preserves native format behavior; needs output-sized local disk and a second I/O pass |
| DuckDB plus registered fsspec filesystem | `COPY` → Python filesystem bridge → backend | Retains DuckDB serialization; qualifies bridge behavior, registration lifetime and overwrite separately |

The third route is demonstrated in the MAD.Prefect reference. DuckDB exposes
`register_filesystem`; its official [fsspec guide](https://www.duckdb.org/docs/current/guides/python/filesystems)
describes this bridge. It does not prove the complete export/publication contract
for a particular version. None of these routes should become a silent fallback
for a failed native MVP export.

**Upstream-confirmed:** DuckDB 1.5 exposes
`connection.execute(query).fetch_record_batch(rows_per_batch=...)`, producing an
Arrow reader. Hold the source connection and its consistent snapshot until all
outputs that require the same materialization have consumed it. Do not rerun
extraction, call `fetchall()`, or collect all batches just to export.
[DuckDB Python API](https://duckdb.org/docs/1.5/clients/python/reference/).

| Format | Concrete serializer path | Resource and compatibility boundary |
| --- | --- | --- |
| Parquet | Create `pyarrow.parquet.ParquetWriter(handle, reader.schema)` once; write batches; close writer to finalize footer | Row-group, dictionary and compression buffers; schema and logical type compatibility; empty results still need a valid file |
| CSV | Create `pyarrow.csv.CSVWriter(handle, schema, write_options=...)`; write batches with one header | Unsupported types must be projected or rejected; specify nulls, quoting, timestamps and nested-value policy |
| NDJSON | Iterate a bounded batch's rows, encode each record, write UTF-8 plus one newline | Python conversion costs; explicit policy for decimals, timestamps, binary, nonfinite numbers and dynamic values |

ParquetWriter accepts file-like output and incremental batches; it need not be
given a destination-specific implementation.
[Arrow ParquetWriter](https://arrow.apache.org/docs/python/generated/pyarrow.parquet.ParquetWriter.html).
CSVWriter supports incremental output, but Arrow's CSV writer does not support
every Arrow type.
[Arrow CSVWriter](https://arrow.apache.org/docs/python/generated/pyarrow.csv.CSVWriter.html),
[Arrow CSV guide](https://arrow.apache.org/docs/python/csv.html).
Python JSON encoding needs explicit framing and custom conversions for types
outside its supported set; `str(value)` is not a general compatibility policy.
[Python JSON documentation](https://docs.python.org/3/library/json.html).

**Recommendation:** define the source as a stable committed-table/snapshot lease,
with native query access and a future batch-reader adapter. Keep Arrow optional
until that writer exists. A snapshot/lease is meaningful; merely storing a table
name could let a later replacement change which materialization gets exported.
Do not pretend all DuckDB types, particularly `VARIANT`, already have an agreed
Arrow/Parquet representation. Qualification must decide projection or rejection.

Streaming bounds accumulation of the complete result, not total memory. Batch
size is a row bound, not a byte bound; one large row can exceed it. Query operators
may buffer/spill, serializers buffer, and remote upload buffers add memory.
Concurrency multiplies those costs. Row ordering requires an explicit ordering
contract; batching alone does not establish it.

## Authentication and object ownership

**Local:** the no-auth strategy supplies no application credentials. OS access
controls still apply. Native preparation creates the parent directory and
provides a path; future fsspec preparation creates a local filesystem and opens a
binary stage handle. Reject an Azure auth selection for this destination rather
than silently ignore it.

**Azure with adlfs:** adlfs is the relevant library name. Its documented interface
supports SAS credentials and `anon=False` credential discovery, including an
explicit async credential from `azure.identity.aio`. It constructs a Blob storage
client, also when accessing a Gen2 account. Authentication remains necessary for
private writes; discovery is an authentication policy, not the absence of one.
[adlfs authentication guide](https://fsspec.github.io/adlfs/index.html).

**Source-specific qualification:** inspected adlfs source reads several
`AZURE_STORAGE_*` defaults. A connection string wins in client construction;
explicit credentials/account keys precede the separate SAS-token route. Default
credential discovery is conditional on other inputs being absent. The source's
default anonymous behavior differs from the guide's anonymous example. Therefore
set intent explicitly and reject conflicting ambient configuration in the future
adapter; do not promise that `anon=False` alone forces the selected identity.
The source also strips protocols and extracts account information independently.
[adlfs implementation](https://raw.githubusercontent.com/fsspec/adlfs/main/adlfs/spec.py).

**DuckDB:** native `credential_chain` uses Azure C++ identity implementations;
its `CHAIN` choices are native provider names. A temporary scoped secret belongs
on the `COPY` connection. SAS can be represented by the connection-string secret
already used in Assquack. An authenticator cannot hand DuckDB a Python credential
object and assume equivalence.
[DuckDB 1.5 Azure docs](https://duckdb.org/docs/1.5/core_extensions/azure),
[pinned native identity construction](https://raw.githubusercontent.com/duckdb/duckdb-azure/ea6ffae3710ec568ce08579dbfc0cddc8c759227/src/azure_storage_account_client.cpp).

Python `DefaultAzureCredential` has its own configurable chain and lifecycle,
including developer-tool identities. Same conceptual intent does not guarantee
the same selected principal, ordering or supported options across Python and
C++. Scope auth names by the existing triple registry key; do not translate
DuckDB's `CHAIN` text blindly into Python options.
[Azure Python async identity API](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.aio.defaultazurecredential?view=azure-python).

Recommended ownership:

| Concern | Owner | Lifetime |
| --- | --- | --- |
| SAS material or identity-selection settings | Credential provider / authenticator | Resolve at execution; never store credentials in manifest or URI |
| Native secret installation | DuckDB authenticator | Inside source-connection lease; remove on success and failure |
| Python credential construction | Future fsspec authenticator | Return an owned credential lease; delegate token refresh to SDK |
| Filesystem/client creation | Destination's writer-specific preparer | Use auth result; own clients it creates |
| Binary stage handle | Prepared destination session | Close after serializer finalization, before publication |
| Serializer and batch reader | Writer / source lease | Writer closes readers and serializer; source owner closes connection |
| Stage name, promotion, cleanup | Destination behavior and its publication adapter | Cleanup only this run's stage; report ambiguous promotion honestly |

fsspec caches filesystem instances unless opted out. A simple per-export design
should use uncached owned instances, or an explicit shared lease manager; do not
close a shared cached client's credential after one export.
[fsspec lifecycle features](https://filesystem-spec.readthedocs.io/en/latest/features.html).
adlfs has asynchronous cleanup helpers for service clients and credentials;
there is no reason to assume every fsspec backend offers one universal synchronous
`close()` API. The adapter must close its actual owned resources and coordinate
with backend finalizers, avoiding double ownership.
[adlfs cleanup source](https://raw.githubusercontent.com/fsspec/adlfs/main/adlfs/utils.py).

Use the ordinary synchronous fsspec file interface for synchronous Arrow/Python
serialization. Async-backed fsspec implementations bridge this to an I/O loop.
An async Assquack entry point can schedule the complete blocking export worker
with its connection owned in that worker. Setting `asynchronous=True` does not
turn Arrow calls into awaitable writes. Explicit async resource shutdown must run
on the appropriate lifecycle/loop; cancellation must wait for in-flight writing
before deleting its stage.
[fsspec async documentation](https://filesystem-spec.readthedocs.io/en/latest/async.html).

## URI and publication reuse

**Recommendation:** destination parsing produces a storage identity, independently
of the selected writer's URI syntax. For Azure, retain account, endpoint kind,
container and object path; for local, retain an actual filesystem path. Validate
relative output paths before joining, including traversal, absolute paths,
Windows drive/UNC paths and encoded delimiters. Recognize Windows paths before
generic scheme parsing. Do not allow an output suffix to change credentials or
storage account.

For example, the agreed DuckDB form
`abfss://exampleaccount.dfs.core.windows.net/lake/exports/orders.parquet`
denotes account `exampleaccount`, container `lake`, key
`exports/orders.parquet`. Its fsspec preparation can directly create
`AzureBlobFileSystem(account_name="exampleaccount", ...)` and open
`lake/exports/orders.parquet`. This avoids treating two libraries' raw URL
parsers as one public contract. The common Hadoop-style
`abfss://lake@exampleaccount.dfs.core.windows.net/...` is a different spelling
and is rejected by current Assquack's username check; accepting it would be a
separate parser decision.

Do not forward every public scheme unchanged to `url_to_fs`: fsspec's inspected
registry lists `az` and `abfs`, while adlfs itself advertises more aliases.
DuckDB also accepts `azure`. Scheme registration and a backend's own parser are
different boundaries.
[fsspec protocol registry](https://raw.githubusercontent.com/fsspec/filesystem_spec/master/fsspec/registry.py).

Reusable destination work includes target validation, stage naming, relative
path policy, publication rules and output receipts. Connection settings versus
filesystem creation cannot be the same preparation implementation. Publication
can share code **if** both writers produce equivalent complete staged objects
and the publication client supports the same required operations and credentials.
Native DuckDB authentication does not automatically authenticate a separate
Python publication client.

Local can stage beside the final file and replace on the same filesystem after
closure. Azure needs a qualified promotion mechanism; generic `fs.mv` does not
establish atomic rename. HNS exposes useful namespace operations, but an adlfs
Blob client does not automatically make use of the Data Lake rename API.
[Azure hierarchical namespace](https://learn.microsoft.com/en-us/azure/storage/blobs/data-lake-storage-namespace).
The [earlier native-writer report](2026-10-23-export-destination-architecture.md)
records the existing native Azure overwrite limitation. This report does not
solve it or approve a new SDK dependency to solve it.

The shared lifecycle needs only proportionate failure rules: publish after all
writer/handle finalization succeeds; record success after publication; remove
only owned stages; retain an unknown outcome when promotion confirmation is
lost. Multiple files and the local manifest are not one cross-system transaction.
fsspec's backend-dependent transactions are not a universal solution.

## Smallest honest typed contract

**Proposed internal contract**, not approved API syntax:

- `CopyTarget(connection, write_uri)` for `Writer[CopyTarget]`.
- `BinaryTarget(handle)` for `Writer[BinaryTarget]`.
- `Prepared[R]`: holds exactly one resource `R`, supplies `finish_write()`,
  `publish() -> Receipt`, `abort()` and `close()`. `finish_write` closes owned
  write handles; publication resources remain alive until `close`.
- `PreparationFactory[R]`: accepts parsed destination, source lease and compatible
  auth factory, returns `Prepared[R]` with partial-construction cleanup.
- `Binding[R]`: pairs `Writer[R]` with `PreparationFactory[R]`; its `run` method
  executes the same generic lifecycle.

A registry resolves a typed binding for `(writer, destination kind)` and the
authenticator for the established triple. Validate compatibility at registration
and lookup. In Python, heterogeneous registry storage may need a narrowly erased
`BoundExport.run(...)` interface, with generics retained inside each binding.
That is preferable to leaking `Any`, optional fields or `isinstance` branches
through shared writing code. The typed auth input to a preparer can likewise be
encapsulated by its registered binding rather than claiming all authenticators
return the same kind of credential.

This keeps destination behavior in charge of preparation; a preparer is a
composed part of that behavior. It is not a new storage-specific serializer.
For MVP only `CopyTarget` and the two native preparation registrations need
exist. Document the resource parameter boundary; do not implement empty future
writers or a capability-negotiation framework now.

## Coherent end-to-end pseudocode

The following is **design pseudocode, not executable or tested Python**. Operations
written in plain English are defined semantic primitives, not hidden production
helpers. The procedures and registration tables below define every custom method
referenced by the coordinating flow. SDK constructor spellings illustrate the
documented APIs; source/schema and publication policies still need implementation
qualification. This example uses a simple committed table of integer/string
columns, so it does not silently claim support for every dynamic type.

```text
record CopyTarget(connection, write_uri)
record BinaryTarget(handle)
record Receipt(final_location)
record Binding[R](writer: Writer[R], prepare: PreparationFactory[R])

Source.open_snapshot():
    acquire an exclusive export connection lease and a consistent read snapshot
    of the intended committed materialization; release both on context exit

Snapshot.query:
    SELECT the validated export columns FROM the quoted committed table

Snapshot.batches():
    execute Snapshot.query on its connection
    return fetch_record_batch(rows_per_batch=65536); caller closes the reader

Registry.resolve(base_uri, writer_name, auth_name):
    select registered parser by scheme, treating plain/Windows paths as local
    parse and validate base_uri into destination identity
    select Binding factory by (writer_name, identity.kind)
    select auth factory by (writer_name, identity.kind, auth_name)
    reject unknown/incompatible selections; return identity, binding, auth factory

Destination.plan(identity, relative_path):
    validate relative_path stays under identity's base
    choose final location and a unique sibling stage owned by this invocation
    return Plan(identity, final, stage, destination publication adapter)

Plan.publish():
    invoke destination publication adapter after stage is completely closed
    local adapter: same-filesystem replace(stage, final)
    Azure adapter: configured, qualified stage-to-final provider operation;
                  await confirmed completion or raise an explicit unknown outcome
    return Receipt(final)

Prepared[R].finish_write():
    close owned output handle if R is BinaryTarget, using its registered closer
    CopyTarget's registered closer is a no-op because COPY has returned
    propagate close failure; do not publish on failure

Prepared[R].publish():
    return Plan.publish()

Prepared[R].abort():
    close remaining stage-write resources; remove only the owned stage where safe
    never delete final output; preserve primary failure and uncertain outcome

Prepared[R].close():
    release owned publication clients, filesystem clients and authentication
    leases in reverse construction order; do not close the borrowed source lease

DuckDBWriter.write(snapshot, target: CopyTarget, format):
    choose validated COPY options by format:
        parquet -> FORMAT parquet
        csv     -> FORMAT csv, HEADER true
        json    -> FORMAT json, ARRAY false
    execute COPY (snapshot.query) TO bound(target.write_uri) (chosen options)
    on target.connection; quote identifiers and bind values

FsspecWriter.write(snapshot, target: BinaryTarget, format):
    with snapshot.batches() as reader:
        parquet: with ParquetWriter(target.handle, reader.schema) as serializer:
                     for batch in reader: serializer.write_batch(batch)
        csv:     with CSVWriter(target.handle, reader.schema,
                                WriteOptions(include_header=true)) as serializer:
                     for batch in reader: serializer.write_batch(batch)
        json:    for batch in reader:
                     for row in batch.to_pylist():
                         write UTF8(json.dumps(row, allow_nan=false)) + newline
                         to target.handle
    unsupported format -> fail before opening destination resources

Binding[R].run(snapshot, plan, auth_factory, format):
    validate writer supports format and source projection
    prepared = prepare(snapshot, plan, auth_factory)
    # prepare cleans any resources it acquires if construction fails
    try:
        writer.write(snapshot, prepared.resource, format)
        prepared.finish_write()
        receipt = prepared.publish()
        return receipt
    on failure:
        prepared.abort() without masking original failure
        rethrow
    finally:
        prepared.close() with truthful reporting if publication already succeeded

ExportDestination.export(source, relative_path, format, registry):
    identity, binding, auth = registry.resolve(base_uri, writer, auth)
    plan = Destination.plan(identity, relative_path)
    with source.open_snapshot() as snapshot:
        receipt = binding.run(snapshot, plan, auth, format)
    record success manifest from receipt; manifest failure does not undo publication
    return receipt
```

The four preparation factory definitions complete the pseudocode:

| Registration | Factory behavior and exact prepared resource |
| --- | --- |
| `(duckdb, local)` | Require no-auth; create parent; build `CopyTarget(snapshot.connection, local_stage_path)`; attach local publication and owned-stage cleanup |
| `(duckdb, azure)` | Load provisioned native extension; invoke selected native authenticator on snapshot connection; format stage as DuckDB Azure URI; build `CopyTarget(snapshot.connection, stage_uri)`; retain secret/publication leases until session close |
| `(fsspec, local)` — future | Require no-auth; create owned uncached `LocalFileSystem`; create parent; open stage `wb`; build `BinaryTarget(handle)`; attach local publication and handle/filesystem cleanup |
| `(fsspec, azure)` — future | Obtain Python auth lease; construct owned uncached `AzureBlobFileSystem` for parsed account with explicit validated auth; open `container/stage-key` as `wb`; build `BinaryTarget(handle)`; retain required publication/client/auth resources through close |

Auth factory definitions used above:

| Authentication registration | Semantic result |
| --- | --- |
| `(duckdb, local, none)` and future `(fsspec, local, none)` | No credential acquisition; no-op auth lease |
| `(duckdb, azure, sas)` | Resolve SAS provider; validate account; install temporary scoped native connection-string secret; lease removes it |
| `(duckdb, azure, credential_chain)` | Install temporary scoped native chain secret; native SDK acquires tokens; lease removes it |
| Future `(fsspec, azure, sas)` | Resolve SAS provider; supply SAS to adlfs through its qualified credential path; reject conflicting defaults; retain only for client lifetime |
| Future `(fsspec, azure, default)` | Create owned `azure.identity.aio.DefaultAzureCredential`; supply explicit credential with authenticated intent; reject conflicting defaults; close owned credential after clients |

These names illustrate compatible registry entries, not a decision about public
default names. `Prepared.finish_write` uses its registered closer, not a runtime
backend switch. Azure publication remains an explicitly required adapter with
the semantics above; this is not a claim that DuckDB or fsspec supplies it today.

For any row in the preparation table the full path is identical: configuration
resolution → plan → snapshot → factory → writer → close output → publish →
release resources → manifest. Only factory, auth and publication registrations
differ. Repeating exports can retain one outer source snapshot instead of opening
one per target, so all destinations see the same committed version.

## Decision for the parent chat

Prefer **composed destination preparation plus one writer per serialization
mechanism**, not one writer per storage backend. The evidence supports sharing
the serializer and coordination. It does not support pretending native and Python
client preparation are identical.

Required refinements to the direction, all internal unless separately exposed:

1. Give prepared resources a writer-specific type and a managed lifetime.
2. Register supported preparation pairs alongside the existing auth triples.
3. Define the committed source/snapshot contract without making Arrow mandatory.
4. Normalize storage identity before translating into native or fsspec paths.
5. Require finalization before publication and explicit ownership of auth/client
   cleanup. Keep Azure safe publication as visible unfinished work.

No public configuration replacement, no fsspec MVP implementation, no duplicated
local/Azure serializers, and no new common backend conditionals are necessary.
Adding another writer can add registrations and adapters while leaving the
coordinator's lifecycle intact. An entirely new writer with different publication
or source requirements may still need deliberate contract evolution; this report
does not promise unlimited extensibility.

## Validation and limitations

Docs-only research. No application code, shared indexes, scope files, branches,
commits or dependencies were changed by this chat. No environment files,
credentials, customer data or live storage were accessed. No cloud execution,
serializer compatibility test, performance measurement or application test suite
was run. Pseudocode was reviewed for control flow and ownership only.

Basic Memory was unavailable through executable discovery, including the reported
uv tool binary directory. No installation or configuration repair was attempted;
the assessment uses the supplied human direction, repository evidence and public
upstream sources. This does not block the technical research.

Documentation validation: the repository local-link/anchor checker passed;
`git diff --check` passed. A separate `git diff --no-index --check` against
`NUL` reported no whitespace errors for this untracked report (its exit status
was 1 because the file is new; Git also emitted its normal LF/CRLF notice).
The prescribed terminology scan found two existing historical references and
none in this report. No Mermaid diagram was added because the comparison tables
and single sequential pseudocode already expose the relevant boundaries.

## Related Docs

- [Native export architecture research](2026-10-23-export-destination-architecture.md):
  native transport evidence and publication limitations.
- [Scope journal](../journal/2026-09-08-mvp-scope.md): recorded public composition;
  latest parent-chat direction further separates destination behavior.
- [Extensibility defects](../epics/01-mvp/06-extensibility-defects.md): existing
  selection and lifecycle implementation gaps.
- [Exports](../exports.md): consumer-facing export behavior.

The shared index is intentionally unchanged under this research chat's explicit
file-scope restriction.
