# Programmatic patterns in Assquack

This guide explains the patterns visible in the current implementation, reviewed
on 6 November 2026. Read it alongside the
[materialization walkthrough](materialization-walkthrough.md): that document
follows one execution; this one explains why the code is arranged that way.

The scope is the standalone package under `src/assquack`, with tests used as
supporting examples. The MAD.Prefect submodule is historical reference material,
not part of this runtime. These are descriptions of the code, not claims that
every pattern was deliberately chosen or recommendations to add abstractions.

Some entries are classic design patterns, such as Strategy and Repository.
Others are architectural patterns or Python idioms, such as pipelines, partial
binding, and context managers. Where a resemblance is only approximate, the
entry says so.

## The main boundaries

Start with these objects and functions. Each owns a different responsibility:

| Component | Responsibility | Useful pattern name |
| --- | --- | --- |
| `asset()` and `AssquackAsset` | Describe a reusable producer and its options. | Decorator factory, callable object, fluent copies |
| `AssetTemplateFormatter` | Resolve declarations using currently available arguments. | Partial evaluation, deferred resolution |
| `AssquackRequest` | Describe one invocation after binding its arguments. | Command message, data transfer object |
| `run_pipeline()` | Coordinate capture, inference, publication, and exports. | Command handler, transaction script, pipeline |
| Publication and schema strategies | Own individual policy decisions within that pipeline. | Strategy |
| `MetadataRepository` | Read and write asset, run, and export records. | Repository |
| `ExportDestination` | Resolve and write outputs through a common contract. | Strategy, polymorphism |
| `AssquackResult` | Provide access to a published table. | Facade, result handle |

The request is the hand-off between the Python-facing API and execution. The
pipeline coordinates the work; the repository owns metadata SQL; strategies own
specific policies. An export destination receives the published table after the
database transaction has succeeded.

## 1. Command message and handler

**Meaning:** package a specific operation into an object, then give it to a
component that knows how to perform it.

**Instance:** [`AssquackAsset._request()`](../src/assquack/_asset.py) constructs a
[`AssquackRequest`](../src/assquack/_request.py) containing
the producer, bound argument references, original templates, mode and cache policy.
The effective configuration is selected when execution starts. Resolve derives
identity and destinations before the producer can mutate its inputs.

The asset then passes that request to the standalone
[`run_pipeline()` function](../src/assquack/_pipeline/__init__.py), which
executes the work. `AssquackRun` holds changing stage progress separately from the
request's inputs.

For example, the reusable `paginated_asset` definition becomes a request to run
that producer with `endpoint="orders"` and publish to `bronze.orders`.
The pipeline does not need to repeat decorator handling or argument binding.

This is a lightweight command-message variant of the Command pattern. The
request has no `execute()` or undo method. It is also a **data transfer object
(DTO)** because it carries related values across the API/execution boundary.
There is no command bus, durable queue, or remote worker. It contains a live
Python callable and potentially arbitrary Python arguments, so it is not a
ready-made serializable job message.

## 2. Pipeline and transaction script

**Meaning:** a coordinator expresses a use case as an ordered sequence of work,
delegating individual steps to focused components.

**Instances:** [`run_pipeline()`](../src/assquack/_pipeline/__init__.py)
coordinates destination resolution, cache lookup, capture, schema inference,
staging, publication, cleanup, and exports.
[`export_result()` and `write_exports()`](../src/assquack/_exports/operations.py)
provide a smaller coordinator shared by manual and automatic exports.

This is a pipeline in the everyday architectural sense. Its stages are explicit
function calls, with branches for cache hits and failures. It is not a generic
pipeline engine whose stages can be rearranged by configuration.

It also resembles a **Transaction Script**: one function organizes a complete
business operation. That name does not mean the entire function runs in one
database transaction. User extraction runs outside the connection lock, and
exports happen after publication commits.

## 3. Strategy: choosing how one part of the work behaves

**Meaning:** place a policy behind a common interface so its caller can use it
without implementing that policy itself.

There are five distinct strategy boundaries:

| Policy | Contract and concrete implementations | Where it is used |
| --- | --- | --- |
| Publication | [`PublicationStrategy`, `ReplacePublicationStrategy`](../src/assquack/_pipeline/publish.py) | The pipeline calls `publish()` for the selected materialization mode. |
| Schema inference | [`SchemaStrategy`, `CurrentRunSchemaStrategy`](../src/assquack/_storage/schemas.py) | The pipeline calls `resolve()` to obtain column projections. |
| Destination behavior | [`ExportDestination`](../src/assquack/_exports/destinations/base.py), [`FileSystemDestination`](../src/assquack/_exports/destinations/filesystem.py) | Export coordination calls `resolve()` and `export()` through the destination contract. |
| Filesystem preparation | [`PreparationStrategy`](../src/assquack/_exports/preparation/registry.py), [`LocalPreparationStrategy`](../src/assquack/_exports/preparation/local.py), [`AzurePreparationStrategy`](../src/assquack/_exports/preparation/azure.py) | A filesystem destination resolves a URI and prepares the environment for writing. |
| Authentication | [`Authenticator`](../src/assquack/_exports/authentication/base.py), [`LocalAuthenticator`](../src/assquack/_exports/authentication/local.py), [`SasAuth`, `CredentialChainAuth`](../src/assquack/_exports/authentication/azure.py) | Preparation enters the selected authentication scope. |

The commit and schema interfaces currently have **one production implementation
each**: `replace` and `current_run`. Their existence does not imply append,
merge, or historical schema reconciliation is available.

Filesystem destinations use **composition**: preparation, authentication, and
writing are separate collaborators. Local and Azure destinations share
`DuckDBFileWriter`; there is no interchangeable file-writer backend registry.
The public destination contract allows other destination types; the preparation
and authentication registries are internal implementation mechanisms.

## 4. Registries and simple factories

**Meaning:** a registry maps a key to an implementation; a factory creates an
instance. They answer “which behavior?” and “how do we construct it?” separately
from the behavior itself.

| Instance | Selection and construction |
| --- | --- |
| [`publication_strategy()`](../src/assquack/_pipeline/publish.py) | Looks up `replace` in a fixed mapping and constructs a commit strategy. |
| [`replace_schema_strategy()`](../src/assquack/_storage/schemas.py) | Looks up `config.schema.replace` and constructs a strategy with this run's resources. |
| [`PreparationRegistry`](../src/assquack/_exports/preparation/registry.py) | Maps URI schemes to existing preparation objects. |
| [`AuthenticationRegistry`](../src/assquack/_exports/authentication/registry.py) | Maps destination kind plus auth method to a factory receiving a credential provider. |

For example, an `abfss` URI selects Azure preparation; `("azure", "sas")`
selects the factory that creates SAS authentication. The `default_preparations()`
and `default_authenticators()` functions assemble the built-in registrations.

These are simple factories, not the inheritance-based GoF Factory Method pattern.
The commit/schema mappings are fixed; the export registries expose internal
registration methods. Unsupported selections fail explicitly.

## 5. Template Method in Azure authentication

**Meaning:** a base class owns the sequence of an operation while subclasses
implement a particular step.

**Instance:** [`AzureAuthenticator.authenticate()`](../src/assquack/_exports/authentication/azure.py)
owns temporary secret naming, registration, the writing scope, cleanup, and
error handling. `SasAuth` and `CredentialChainAuth` override `register()` to
create the appropriate kind of DuckDB secret.

This differs from Strategy: the destination selects an authentication strategy;
inside the Azure family, inheritance reuses one lifecycle while varying secret
registration. The same classes therefore participate in both patterns.

## 6. Null Object for local authentication

**Meaning:** supply an object that deliberately does nothing while satisfying
the same interface as collaborators that perform real work.

**Instance:** [`LocalAuthenticator`](../src/assquack/_exports/authentication/local.py)
returns `nullcontext()` from `authenticate()`. Local preparation can always
enter an authentication context without branching on whether authentication is
needed. Its fingerprint inputs are empty, and the local authentication factory
does not load the credential provider.

## 7. Dependency injection and structural interfaces

**Meaning:** supply a component's dependencies instead of making it discover
all of them internally. Python `Protocol` contracts allow compatible objects
without requiring inheritance from a shared concrete base class.

**Instances:** `MetadataRepository` receives its connection and catalog;
`CurrentRunSchemaStrategy` receives its connection, fragment callback, and reader;
authentication factories receive a
[`CredentialProvider`](../src/assquack/_exports/authentication/credentials.py).
Commit, schema, preparation, authentication, and credential-provider contracts
use protocols. `ExportDestination` instead uses an abstract base class.

The provider separates loading credential values from using them to authenticate.
[`test_export_destinations.py`](../tests/test_export_destinations.py) supplies
synthetic providers and preparation/authentication implementations to exercise
these boundaries without contacting Azure.

There is no dependency-injection container. Wiring is ordinary Python arguments
and object composition. Credential-provider injection is internal, not a public
configuration option; the shared DuckDB engine is also not an injected backend.

## 8. Repository for metadata access

**Meaning:** expose storage operations in the language of the domain rather than
making callers construct persistence queries themselves.

**Instance:** [`MetadataRepository`](../src/assquack/_storage/metadata.py) offers
methods such as `save_asset()`, `start_run()`, `find_current_publication()`,
`mark_run_successful()`, and `record_export()`.

The pipeline can say “mark this run successful” without knowing which metadata
tables and timestamp columns need updating. The repository is specifically for
metadata; it does not abstract every DuckDB query, staging operation, or asset
table. It uses explicit SQL and small record dataclasses, not an ORM.

## 9. Transaction boundary and staged publication

**Meaning:** prepare a candidate result separately, then make the authoritative
change and its metadata updates succeed or fail together.

**Instance:** [`create_shaped_staging()`](../src/assquack/_pipeline/stage.py)
builds a candidate table. The Publish block in
[`run_pipeline()`](../src/assquack/_pipeline/__init__.py) opens
[`transaction()`](../src/assquack/_storage/database.py), calls
[`ReplacePublicationStrategy.publish()`](../src/assquack/_pipeline/publish.py) through
the work helper, and saves Publish success. An exception rolls back the table,
publication metadata and stage success together.

This provides the atomic grouping associated with a **Unit of Work**, but there
is no ORM-style unit-of-work object tracking pending entities. It is a direct
database transaction. See the rollback example in
[`test_schema_replacement.py`](../tests/test_schema_replacement.py).

Exports are outside that transaction. A failed export can leave a successfully
published table and some completed files. This is not a distributed transaction,
two-phase commit, or a saga that compensates by undoing publication.

## 10. Context managers for scoped resources

**Meaning:** pair acquiring a resource with releasing it on both normal and
exceptional exits. Python expresses this with `with` and `async with`.

| Instance | Scope it owns |
| --- | --- |
| [`database_operation()`](../src/assquack/_storage/database.py) | Holds the shared connection's reentrant lock for a synchronous operation. |
| [`transaction()`](../src/assquack/_storage/database.py) | Commits or rolls back a database transaction. |
| [`resolve_exports()`](../src/assquack/_exports/resolution.py) | Copies the configured destination for an operation and releases its captured resolution state on exit. |
| Local/Azure `prepare()` and `authenticate()` | Prepare writing and delimit temporary authentication resources. |

The pipeline uses `database_operation()` and `CONNECTION_LOCK`. DuckDB retains
its database attachments for the process lifetime; the removed standalone file
lock is not part of the execution model.

Fragment writers use a related explicit lifecycle: `finish()` completes capture
and `abort()` closes an open file on failure. Failed generators are explicitly
closed by `write_fragments()` so their cleanup runs in the consuming task.

## 11. Decorator factory and callable asset objects

**Meaning:** Python decoration transforms a function definition into an object
with additional behavior.

**Instance:** [`asset()`](../src/assquack/_decorator.py) returns a `decorate()` closure,
which constructs `AssquackAsset` around the producer function. The resulting
object is callable through `__call__()`, which captures the request immediately
and returns the pipeline coroutine to await. It also offers
binding, caching, query, and existence operations.

This is a Python decorator factory and a callable wrapper. It is not a textbook
GoF Decorator that preserves an identical component interface: the wrapped
producer might return rows, whereas calling the asset returns an
`AssquackResult` after materialization.

## 12. Fluent copies and partial binding

**Meaning:** derive a specialized definition from an existing one, keeping the
original available for reuse. Fluent methods return objects that can be chained.

**Instances:** [`with_arguments()`, `with_options()`, and `cache_first()`](../src/assquack/_asset.py)
use `dataclasses.replace()` to construct new asset definitions.

```python
orders = paginated_asset.with_arguments(endpoint="orders").cache_first()
customers = paginated_asset.with_arguments(endpoint="customers")
```

Binding resembles `functools.partial`: it records arguments for a future call.
These methods also resemble a fluent builder, but there is no separate builder
object or final `build()` step. Each returned object is already an asset.
Resolve builds a separate resolved asset description and qualifies its table
reference; it does not alter the request's original templates.

Asset definitions remain shallow fluent copies with independently copied export
options and retained original templates. Request arguments and configuration use
ordinary Python references. Formatting one derived asset cannot rewrite its siblings,
but a producer mutating an argument changes the caller's object too.

## 13. Partial evaluation and deferred template resolution

**Meaning:** resolve what is known now, preserve expressions that need more
information, then require a concrete answer at execution time.

**Instances:** [`AssquackAsset.__post_init__()`](../src/assquack/_asset.py) formats
table, name, and export templates using bound arguments and function defaults
with `allow_partial=True`. Module-level decoration and `.with_arguments()` both
run this step. [`AssetTemplateFormatter`](../src/assquack/_templates.py) preserves
missing named fields using `_PartialFormatArguments` and `_Placeholder`.

For `table="bronze.{endpoint}"`, the original definition can retain the
placeholder. Binding `endpoint="orders"` immediately exposes `bronze.orders`,
before the producer runs. If a replacement contains another placeholder, the
formatter makes further passes; repeated unresolved forms stop partial
formatting or fail strict formatting.

At invocation, `_request()` binds and captures the actual call and **original**
templates retained in `_templates`. Resolve then strictly formats those captured
templates. It does not merely fill remaining holes in
the previously rendered strings. A call-time keyword can therefore override a
previously bound value. Missing arguments or unresolved fields fail before
materialization. Nested asset arguments can contribute template values without
executing those assets; direct arguments take precedence.

This is staged resolution, not a general lazy computation framework. It is also
different from lazy DuckDB query execution, described below.

## 14. Adapters, type dispatch, and iterator consumption

**Meaning:** accept different input representations and translate them into a
common form for the next stage.

**Instance:** [`write_fragments()`](../src/assquack/_storage/fragment_writer.py)
dispatches on producer output types. Mappings use the Python NDJSON writer;
DataFrames and Arrow inputs become DuckDB relations; existing relations use
native DuckDB `COPY`; returned assets/results are materialized or queried.
Synchronous and asynchronous iterables are consumed recursively.

This is adapter behavior implemented by a dispatcher function, rather than an
Adapter class per input type. Specific inputs are recognized before generic
iteration so, for example, a mapping becomes one row rather than a sequence of
keys. Optional pandas and Arrow imports occur only for their corresponding input
objects. The pipeline separately awaits an awaitable producer result.

The iterator approach allows yielded pages to be consumed incrementally. It does
not promise every producer or native relation has bounded memory use.
[`test_fragment_inputs.py`](../tests/test_fragment_inputs.py) demonstrates the
accepted input shapes.

Another small adapter is `load_provider()` in
[credentials.py](../src/assquack/_exports/authentication/credentials.py): it
bridges an asynchronous provider-loading interface to synchronous export code.
It uses a worker thread when the caller already has an event loop.

## 15. Facade, result handle, and native lazy queries

**Meaning:** expose a small interface that delegates to the components needed to
perform a task.

**Instances:** [`AssquackAsset`](../src/assquack/_asset.py) is the facade for asset
execution. [`AssquackResult`](../src/assquack/_result.py) provides `query()`,
`to_df()`, `exists()`, and `export()` over a published table. The package's
[`__init__.py`](../src/assquack/__init__.py) collects the supported public imports.

The result is a handle containing table and run information, not a container
holding every row. Its table reference is not a historical snapshot: later
replacement of that table can change what a query reads.

[`execute_query()`](../src/assquack/_query.py) returns a native lazy
`DuckDBPyRelation`. DuckDB owns query planning and chaining; Assquack supplies
the quoted source table. Fetching results or capturing the relation with `COPY`
executes it. `asset.query()` first runs the asset/cache path, while
`result.query()` queries the existing table without re-extraction.

## 16. Value objects and typed records

**Meaning:** give related values a named structure rather than passing loosely
related strings, tuples, or dictionaries everywhere.

**Instances:** [`TableReference`](../src/assquack/_storage/tables.py) groups
catalog, schema, and table names and supplies quoted SQL. `FragmentFile` lives
with the fragment writer; `ProjectionSpec` lives with schema inference.
[`records.py`](../src/assquack/_storage/records.py) defines `AssetRecord` and
`PublicationRecord`; [`ExportTarget`](../src/assquack/_exports/models.py) describes
one resolved output. `AssquackRequest` holds the captured call, while `AssquackRun`
holds changing execution state. These are
typed records/DTOs; their existence does not imply a full domain-driven design.
[`AssquackConfig`](../src/assquack/_config.py) and its nested settings models
provide a related boundary for validation and normalization. For example, a
string export base becomes a `FileSystemDestination`, leaving downstream code
with a consistent list of destination objects.

## 17. Cache lookup and resumable export progress

**Meaning:** reuse an acceptable existing result, and record completed side
effects so a retry can avoid repeating known work.

**Instances:** [`find_cached_run()`](../src/assquack/_storage/cache.py) checks successful
publication metadata, matching asset identity, table existence and optional expiry.
The current-publication table identifies which run occupies the physical table. The pipeline bypasses
extraction on a cache hit. This resembles cache-aside control flow, but the
reused data is the authoritative DuckDB table, not a separate cache service.

[`write_exports()`](../src/assquack/_exports/operations.py) records each output
after a successful write. On the cache path, it skips outputs already recorded
for the run, resolved URI, and destination fingerprint. The
[fingerprint](../src/assquack/_exports/destinations/fingerprint.py) identifies
resolved destination configuration independently of individual output paths.

This is checkpointed progress, not exactly-once delivery. A process can fail
after writing a file but before recording its metadata, causing a later rewrite.
Fresh materializations and manual exports do not use the cache path's
`skip_completed=True` behavior. Metadata is a progress record, not an outbox
drained by an independent worker.

## 18. Explicit lifecycle and bounded recovery

**Meaning:** record which phase work has reached and recover only within defined
boundaries.

**Instances:** `AssquackRun` records five explicit stage columns and an invocation
outcome. Publication success remains recorded independently of a later Export
failure. The main pipeline has an explicit try/except block for each stage,
with visible status assignments and state saves. Helpers perform the operations;
the main function owns Publish's transaction and its success save.
[`recover_interrupted_runs()`](../src/assquack/_storage/metadata.py) marks
abandoned runs failed while excluding known active runs. Fragment files progress
from `.partial` to `.ndjson`, with `_COMPLETE` marking completed capture.
[`prune_fragments()`](../src/assquack/_storage/retention.py) uses catalog
ownership and chronology to remove eligible old evidence.

These are explicit state transitions, not the GoF State pattern with a separate
class per state. Retained fragments support diagnosis; a retry starts fresh
extraction rather than resuming a partially consumed producer.

[`JsonReadRecovery`](../src/assquack/_storage/json_reader.py) is a
specific bounded-retry mechanism: on the recognized oversized-record error, it
measures fragment records, adjusts the reader limit, and retries once. The
run-local object shares that measured limit between inference and staging.
Unrelated errors propagate. There is no general retry/backoff or circuit-breaker
framework around producers.

## 19. Shared engine ownership

**Meaning:** coordinate access to one process-wide resource and keep its
initialization rules in one place.

**Instance:** [`database.py`](../src/assquack/_storage/database.py) uses DuckDB's
actual default connection, tracks attached catalogs and bootstrapping, and
protects synchronous operations with an `RLock`. The first attachment establishes
shared engine settings; incompatible explicit settings are rejected later.

This has singleton-like lifetime, but it is not a Singleton class, connection
pool, or one connection per asset. Multiple configured databases attach to the
same engine. The import-time initializer runs only when placement environment
variables opt into attachment; resource/export settings alone do not do so.

[`tests/conftest.py`](../tests/conftest.py) resets the shared engine state around
each test. That fixture is a useful illustration of the isolation work required
when production code owns process-wide state.

## Where to start reading the code

Follow [`_api.py`](../src/assquack/_asset.py) into the
[request model](../src/assquack/_request.py), then read
[`pipeline.py`](../src/assquack/_pipeline/__init__.py). Once the sequence
is clear, inspect the strategy and repository calls it delegates to. For exports,
follow [`FileSystemDestination`](../src/assquack/_exports/destinations/filesystem.py)
through preparation, authentication, and the fixed DuckDB writer.

The useful distinction throughout is between a reusable definition, the values
for one invocation, the coordinator executing it, and the collaborators that own
specific policies or resources.

## Related Docs

- [Materialization walkthrough](materialization-walkthrough.md): one concrete
  execution from module import to published tables and exports.
- [Developer API](developer-api.md): supported public contracts and usage.
- [Materialization lifecycle](materialization-lifecycle.md): execution phases,
  caching, and failure behavior.
- [Storage model](storage-model.md): database ownership and metadata tables.
- [Schema inference](schema-inference.md): how current-run types are selected.
- [Exports](exports.md): destination behavior and compatibility artifacts.
- [Configuration](configuration.md): validated settings and precedence.
