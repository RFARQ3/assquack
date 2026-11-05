# Assquack Developer API

Assquack is a standalone, framework-agnostic Python package for DuckDB-native
data assets.

Package import:

```python
from assquack import asset
```

Detailed usage examples live in [developer-examples.md](developer-examples.md).
This document defines the developer-facing contract.

## Package

To inspect publication history, query `assquack_meta.assets` for
`first_materialized_at` and `last_materialized_at`. These nullable timestamps
describe successful publication, not registration or attempted execution.
Use `assquack_meta.runs` for attempts and statuses. See the
[storage contract](storage-model.md#asset-tables) for cache and failure semantics.

The target package name is `assquack`.

The core package must not depend on Prefect or any other orchestrator. Runtime
adapters can call Assquack later, but the public API should work in ordinary
Python processes, notebooks, tests, CLIs, and orchestration frameworks without
changing asset definitions.

## `@asset`

`@asset` decorates a Python function and returns an `AssquackAsset` definition.
Calling the asset materializes it and returns an `AssquackResult`.

The required table-first signature implements the 28 October 2026 clarification
and supersedes the positional-export API. See the
[decision record](journal/2026-09-08-mvp-scope.md#table-first-decorator-decision-27-october-2026).

Minimal shape:

```python
@asset("sales.orders")
async def orders():
    ...
```

The first positional argument is the DuckDB schema/table name. It can instead be
written explicitly as `table="sales.orders"`. The remaining arguments are
keyword-only, ordered `mode`, `name`, `export`, then `cache_expiration` and `config`. Database-file
placement remains configuration-level. Table is required: omission raises
`TypeError`; `None`, empty, and blank table names are rejected before
materialization. Explicit table templates remain supported. Keyword arguments
may be supplied in any order.

Typed contract shape:

```python
from typing import Literal, TypedDict
from datetime import timedelta


AssetMode = Literal["replace"]
ExportFormat = Literal["parquet", "json", "csv"]


class ExportSpec(TypedDict):
    uri: str
    format: ExportFormat

asset(
    table: str,
    *,
    mode: AssetMode = "replace",
    name: str | None = None,
    export: str | ExportSpec | None = None,
    cache_expiration: timedelta | None = None,
    config: AssquackConfig | None = None,
)
```

The implementation may add narrowly scoped options over time, but database
placement must remain configuration-level rather than a decorator argument.

## Export Aliases

Export declarations use the keyword-only `export=` argument, after `name`.
For example:

```python
@asset("sales.orders", export="sales/orders.parquet")
@asset(table="sales.orders", export="sales/orders.parquet")
```

These two declarations are equivalent. With `export` omitted, the asset has no
automatic export declaration. Relative export paths retain `mad://` alias
resolution; that behavior does not make them positional decorator arguments.

## File Type Aliases

An export target can be either a path or a file type alias.

- `export="parquet"` means export the asset as Parquet under the configured
  default export base, deriving the concrete path from the asset name.
- `export="json"` and `export="csv"` are file type aliases with the same
  default-path behavior.
- A path suffix such as `.parquet`, `.json`, or `.csv` selects that export
  format from the explicit path.
- A compound alias such as `bronze/orders.parquet|json` requests an additional
  legacy-compatible export format while keeping the explicit path as the primary
  target.

Export aliases affect compatibility artifacts only. The canonical materialized
state is the DuckDB table managed by Assquack.

Export aliases do not determine the asset name, and the resolved export
destination is not part of the canonical identity. Changing an
export base URI or moving from local exports to ADLS should not invalidate the
DuckDB asset table.

## ExportSpec

`ExportSpec` is the structured form of an export target. It should stay small in
the bootstrap API:

- `uri`: the explicit export URI or configured-alias path to write.
- `format`: one of `"parquet"`, `"json"`, or `"csv"`.

The MVP can model this as a `TypedDict`, frozen dataclass, or Pydantic model,
but the public type should remain concrete enough for autocomplete and static
checking. Avoid accepting arbitrary dictionaries for export configuration at the
asset decorator boundary.

## Optional Overrides

A table must be supplied. Asset names default to the resolved table template
(for example, `sales.orders`), and
materialization mode defaults to `replace`. Developers can set:

- `name=` is the stable logical asset name used in manifests, metadata, and
  derived default export paths.
- `table=` is the stable DuckDB schema/table target exposed for querying.
- `mode=` selects the materialization strategy. The default is `replace`.

The MVP mode is `replace`: build staging tables for the run, then
transactionally replace the current table. Planned follow-up modes include
`append`, `merge`, and `snapshot`.

Because public contracts should be autocomplete-friendly, `mode` should be typed
as a narrow literal or enum rather than accepted as an arbitrary string. The MVP
contract is intentionally small: only `"replace"` is valid.

The declared mode is carried into execution and stored as
`assquack_meta.assets.materialization_mode`. Unsupported modes are rejected
before extraction or database writes. Commit strategies are framework-owned;
there is no public strategy registration or injection API.

There is no `database=`, `database_path=`, `artifacts_dir=`, or equivalent
per-asset database placement argument. Database path, locking, DuckDB options,
and export base URI belong to Assquack configuration and deployment.

## Calling Assets

Calling a decorated asset executes the materialization lifecycle and must be
awaited: `result = await orders()`. The wrapped producer may be synchronous or
asynchronous; a successful awaited call returns an `AssquackResult`.

`AssquackResult` represents the latest materialized table for that asset and
should provide:

- `.query(sql=None, params=None)` to query the result table.
- `.to_df()` to collect results into a pandas DataFrame where pandas is
  available.
- `.export(destination)` to write to a string path or an `ExportDestination`,
  returning one URI string or an ordered list. Filesystem destinations support
  Parquet, CSV and JSON. See [manual exports](exports.md#manual-exports).
- `.exists()` to check whether the latest successful materialization is
  available.

The result is a table reference plus metadata, not a raw JSON dump or a required
Parquet fragment.

## Querying

Assets and results expose `.query(sql=None, params=None)`.

When called on an asset, `.query()` first invokes that bound asset using its normal
cache policy and optional `assquack_config` override, then queries the returned
result. A first query materializes a missing asset; an uncached query runs the
source again. Bound arguments and table templates apply exactly as for an asset
call. Cache hits skip extraction; expired caches refresh through normal execution.

`AssquackResult.query()` and `assquack.query()` read existing data without invoking
source extraction. Use a saved result or a general query to inspect publication
after a failed run. Source/materialization failures propagate to the caller.
Assquack does not pre-validate SQL statement counts or statement types; DuckDB
parses the SQL used by the query implementation.

Within SQL passed to `.query()`, Assquack should provide a stable relation alias
for the current table. The examples use `data`:

```python
await orders.query("SELECT count(*) FROM data")
```

`params` should be passed through to DuckDB parameter binding rather than
string-interpolated by Assquack.

## General database queries

`await assquack.query(sql, params=None)`, `await asset.query(...)` and
`await result.query(...)` return actual `duckdb.DuckDBPyRelation` objects on
DuckDB's default connection. Query calls accept SQL and optional
bound parameters. SELECT validation is a convenience, not a security sandbox:
functions invoked by trusted application SQL can have side effects.

### Explicit config context

Give each database a public catalog name in configuration. Constructing configs
or decorating assets performs no database I/O. Assets attach their effective
config on first materialization/query/existence check. Attach other configs
explicitly before using them:

```python
people = AssquackConfig(home=Path("./people"), attachment_name="people")
people.attach()  # synchronous, idempotent for the same name and path
rows = await assquack.query("SELECT * FROM people.hr.employees")
```

The default attachment name is `assquack`. Different database files need different
names. Reusing a name for another path, or another name for an already attached
path, raises an error. See [configuration](configuration.md#database-attachments)
for environment settings and shared-engine controls.

With exactly one attached user database, Assquack queries accept `table` and
`schema.table` and bind them to that database. With multiple user databases,
physical references require `database.schema.table`, even if a short name is
unique. DuckDB's system, temp and default memory catalogs do not count as user
attachments. With no user attachments, native DuckDB resolution applies.
Two-part names mean `schema.table`; use all three parts to name a database.

```python
rows = await assquack.query("SELECT * FROM people.hr.employees WHERE id = ?", [42])
```

`assquack.use_config(config)` requires an already attached config, but does not
override this naming rule or select asset write destinations. Asset configuration
still selects writes. Scopes restore after exceptions and cancellation; child
tasks inherit their context. No query changes the connection's current database.

Query-local CTEs, the asset query's `data` alias and Python replacement scans are
exempt from physical qualification. A name colliding with a physical table/view
must follow the physical rule; it cannot silently fall back to a memory table.
Recursive CTE anchors resolve physical names before the recursive name is visible.
Unqualified `query_table` follows the same rule for literal or parameterized table
names and lists, including snapshot lists with `union_by_name`.
Caller parameters remain unchanged, including when reused elsewhere in the query.
Computed table-name expressions must instead be supplied as string/list parameters.
Qualified `query_table` function/macro calls (such as `main.query_table`) fail
clearly; use unqualified `query_table` with fully qualified table-name arguments.
Other function/macro names and object names inside strings are not rewritten:
qualify those explicitly, for
example `nextval('people.hr.employee_id')`. Assquack SQL must be supported by
DuckDB's JSON SQL serializer; unsupported forms fail clearly. Fully qualified
`duckdb.query()` bypasses Assquack's naming checks.

### Chaining queries by Python variable name

```python
import duckdb

employees = await employee_asset.query()
departments = await department_asset.query()
joined = duckdb.query(
    "SELECT e.id, d.name FROM employees e "
    "JOIN departments d ON e.department_id = d.id"
)
summary = await assquack.query("SELECT count(*) FROM joined")
```

The assets may publish to different registered databases. Use native methods such
as `.filter()`, `.project()`, `.fetchall()` and `.df()` directly. Native
`relation.query(alias, sql)` keeps DuckDB's own signature; there is no result
wrapper or `.relation` property. The initial asset query retains its statement-local
`data` alias. DuckDB's normal CTE/table precedence over Python variables applies;
use distinct variable names to avoid ambiguity. Assquack enables DuckDB's native
caller-stack replacement scans; it does not convert results or modify caller locals.

### Results and lifetime

Results are lazy, not frozen snapshots. After a table replacement a retained
relation can read new rows or fail if its bound schema is no longer compatible.
No root query refresh or explicit cleanup is required. Results remain usable
after leaving a config scope and after subsequent asset executions.

Assquack retains attachments for the process lifetime and never closes the default
connection after an operation. Do not close, replace, detach, or manually manage
transactions on that connection while Assquack uses it. Its file handles can
prevent another process or an independently opened connection from accessing the
same files, particularly on Windows. Independent connections remain useful for
other databases, but their relations cannot be captured or joined on this engine.
Foreign relation capture raises a clear error; there is no silent transport copy.

Database operations run synchronously on the calling thread. Assquack coordinates
its own complete publication transactions, with no transaction or lock across
awaited source execution. Arbitrary multi-threaded calls directly to DuckDB's
global connection bypass that coordination and are unsupported. See
[storage concurrency](storage-model.md#concurrency) and
[direct capture](materialization-lifecycle.md#fragment-capture).

Table globbing remains deferred. No separate cross-database query API is needed:
use qualified catalogs or native relations on the shared connection.

## Cache First

Set `@asset("sales.orders", cache_expiration=timedelta(hours=2))` to reuse a
valid materialization on ordinary calls until two hours after publication (the
exact TTL boundary is expired). Omitted
or `None` expiration leaves normal calls uncached; zero also disables reuse.
Negative durations raise `ValueError` at declaration. Bound assets retain this
default.

`.cache_first()` returns an asset callable that prefers the latest valid cached
materialization. If metadata says the cache is valid and the current table still
exists, the callable returns an `AssquackResult` without rematerializing the
wrapped function.

Fluent overrides return a new definition without changing the decorated asset:
`.cache_first(timedelta(minutes=10))` overrides its TTL, `.cache_first()` (or
explicit `None`) allows unlimited reuse, and `.cache_first(timedelta(0))` forces
a refresh. Cache policy is not part of asset identity, so these views share the
same materialization.

Cache state is based on Assquack metadata tables and the DuckDB current table,
not only on whether an exported file exists.

## Bound Arguments

`.with_arguments(**kwargs)` returns an argument-bound asset callable. Bound
arguments participate in:

- calling the wrapped Python function;
- resolving templated export aliases, names, and table conventions;
- deriving the stable asset identity for metadata and cache lookup.

Bound arguments preserve legacy path-template ergonomics without making the API
depend on a particular orchestration framework.

## Accepted Inputs

Asset functions may return or yield batches. Supported inputs are:

- Python mappings and lists of mappings;
- sync and async iterables;
- pandas DataFrames;
- Arrow Tables and RecordBatches;
- DuckDB relations owned by the default connection;
- `AssquackResult` table references and `AssquackAsset` definitions.

Asset authors must decode HTTP responses before returning their JSON data.
Results read their materialized table directly; assets execute with their normal
cache preference and the calling asset's configuration. Mixed sync/async yields
are supported, and empty inputs produce no fragments.

Assquack treats these values as observed data for the current run. The
materializer normalizes them into local NDJSON fragments, uses DuckDB's native
JSON reader to infer the current run schema, and updates the current table
according to the asset's mode.

## Related Docs

- [Developer Examples](developer-examples.md): short usage examples for this
  API contract.
- [Materialization Lifecycle](materialization-lifecycle.md): what happens when
  an asset is called.
- [Exports](exports.md): how keyword export paths and file type aliases become
  compatibility artifacts.
- [Configuration](configuration.md): where database paths and export bases are
  configured.
- [Storage Model](storage-model.md): the tables and locks behind asset results.

## Asset config and option copies

Assets accept a default
`AssquackConfig` through `@asset(..., config=...)`, avoiding repeated config
arguments at every execution call.

`with_options()` accepts the same settings as the decorator: `table`,
`mode`, `name`, `export`, `cache_expiration`, and `config`. It returns a new asset,
preserving its function, bound arguments, and unspecified settings. The original
asset remains unchanged. Neither configuration nor binding executes the asset.
A separate `with_config()` convenience method is not part of this goal.

The distinction is what each setting controls:

| Category | Settings |
| --- | --- |
| Execution config | Home, database path, DuckDB resources, fragment settings, export destinations |
| Asset options | Table, mode, name, cache expiration, export path/format, attached config |
| Function arguments | Inputs to the wrapped function, bound through `with_arguments()` |

Config supplies export destination bases; the asset's export option supplies its
output path/format. Environment variables are a configuration source, not the
boundary between config and asset options.

Attach a config once, then copy settings independently:

```python
@asset(
    table="bronze.{endpoint}",
    export="bronze/{endpoint}.parquet",
    cache_expiration=timedelta(hours=2),
    config=local_config,
)
async def api_asset(endpoint: str):
    return paginate_api(endpoint)

profiles = api_asset.with_arguments(endpoint="profiles")
prod_profiles = profiles.with_options(config=prod_config)
uncached_profiles = profiles.with_options(cache_expiration=None)

await profiles()
await prod_profiles()
```

An explicit execution-time config takes precedence over the asset's attached
config. Without either, ordinary `AssquackConfig` defaults apply. This is config
selection, not a merge of two config objects. Omitted options retain their values;
explicit `None` clears nullable options such as export, cache expiration or
attached config. Required options remain validated as in the decorator.

This follows MAD.Prefect's copy-and-customise behaviour without adding a Prefect
dependency. See [configuration](configuration.md) for the config model and
[implementation tracking](epics/01-mvp/03-query-and-cache.md#asset-options-and-attached-config)
for acceptance criteria.

Execution, asset `query()` and `exists()` all use this config selection.
Passing `assquack_config=None` means no call override. `with_options(name=None)`
restores the table-derived default. Changing `table` updates a default-derived
name and preserves an explicit `name`. Names retain their original templates:
`@asset("bronze.{endpoint}")` bound with `endpoint="profiles"` has the name
`bronze.profiles`; rebinding or passing execution arguments resolves the name
from the same table template. An explicit cache expiration reset disables
cache reuse, including on a `cache_first()` copy; omitting it retains the fluent
cache policy.

Returned or yielded asset definitions execute with the parent materialization
config as an explicit override. Direct Python calls to another asset select their
own explicit or attached config; there is no ambient config propagation. Attach
the shared config to each directly called dependency.
