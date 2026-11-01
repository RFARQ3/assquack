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
[storage contract](storage-model.md#asset-tables) for cache, failure, and existing
database migration semantics.

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

Calling a decorated asset executes the materialization lifecycle. The call may
be sync or async depending on the implementation and the wrapped function, but
the expected result concept is the same: a successful call returns an
`AssquackResult`.

`AssquackResult` represents the latest materialized table for that asset and
should provide:

- `.query(sql=None, params=None)` to query the result table.
- `.to_df()` to collect results into a pandas DataFrame where pandas is
  available.
- `.export_parquet(path=None)` to write Parquet to each configured destination,
  returning one URI string or a list of strings. With no configured destinations,
  it uses the default local export base. See [Exports](exports.md).
- `.exists()` to check whether the latest successful materialization is
  available.

The result is a table reference plus metadata, not a raw JSON dump or a required
Parquet fragment.

## Querying

Assets and results expose `.query(sql=None, params=None)`.

When called on an asset, `.query()` should query the latest available current
table for that asset. When called after materialization on an `AssquackResult`,
it should query that result's table.

Within SQL passed to `.query()`, Assquack should provide a stable relation alias
for the current table. The examples use `data`:

```python
await orders.query("SELECT count(*) FROM data")
```

`params` should be passed through to DuckDB parameter binding rather than
string-interpolated by Assquack.

## General database queries

Revised MVP direction, 30 October 2026: `await assquack.query(sql, params=None)`
queries tables within one configured database. Multiple assets may use different
`AssquackConfig` objects and publish to different databases in the same project;
each general query selects only one database.

`assquack.query` returns an `AssquackQueryResult`. It accepts one
SELECT statement and optional positional or named parameters, bound by DuckDB.
It reads an existing database without executing assets, creating a missing
source file, or bootstrapping Assquack metadata.

Config selection uses this order:

1. The innermost explicit `assquack.use_config(config)` scope.
2. The currently executing asset's effective config, including execution overrides.
3. Process environment configuration. Outside these scopes, require nonblank
   `ASSQUACK_DATABASE_PATH` or `ASSQUACK_HOME`; otherwise raise a clear error.
   Resource settings alone do not select a database. No `.env` files are loaded.

### Explicit config context

Use the synchronous context manager to select a query database:

```python
with assquack.use_config(reporting_config):
    rows = await assquack.query("SELECT * FROM bronze.orders")
```

The explicit scope controls general query reads only. It does not change asset
publishing configs or ordinary child asset config selection. It remains higher
priority while nested assets execute. Nested scopes restore the previous config,
including after exceptions or cancellation; concurrent tasks have independent
contexts. Child tasks inherit the context active when they are created.
Asset context covers deferred sync and async generator consumption too.

### Chaining queries by Python variable name

Both `await assquack.query(...)` and `await some_asset.query(...)` return
`AssquackQueryResult`. Its synchronous `.query(sql, params=None)` method runs
another SELECT on the same owning connection:

```python
with assquack.use_config(reporting_config):
    reports = await assquack.query("SELECT * FROM bronze.reports")
    latest = reports.query(
        "SELECT * FROM reports WHERE snapshot_date = CURRENT_DATE"
    )
    summary = latest.query("SELECT COUNT(*) FROM latest")

print(summary.fetchall())
```

Use the actual Python variable name in SQL. No alias argument, registration,
mandatory `data` name, or caller cleanup is needed. For the initial asset query,
`await reports_asset.query("SELECT * FROM data")` retains the existing `data`
alias for that asset's table; subsequent wrapper queries use Python variables.

Wrapper lookup uses only the immediate caller's locals and globals, with locals
winning. Pass a result as an argument when querying it inside a helper function:

```python
def count_reports(reports):
    return reports.query("SELECT COUNT(*) FROM reports")
```

Assquack adapts wrapper references to native relations in a temporary Python
namespace during binding. It does not mutate caller variables, register views,
walk other stack frames, or change DuckDB's module default. DuckDB's normal SQL
resolution applies: a SQL CTE or database table takes precedence over a same-named
Python variable. Use distinct variable names where that could be ambiguous.
Plain `duckdb.query` does not recognise the wrapper; chain with its `.query()`.

### Results and lifetime

The small wrapper surface is `.query()`, `.fetchall()`, `.fetchone()`, and
`.relation`. Fetch methods retain DuckDB's normal cursor-consumption behavior.
Use `.relation` for native operations such as `.filter()`, `.project()`, types,
columns, or native export methods. Return or yield either the wrapper or a native
relation from an asset; both use the existing native fragment writer without
pandas, mandatory Arrow, or eager result copies.

Live wrappers from the same task and execution reuse a connection keyed by the
resolved database path. Therefore independently queried assets from that database
can be joined by variable name. Different processing/export settings do not split
that connection; the first query opening it supplies DuckDB resource settings.
Asset executions establish their own connection scope, restoring the parent's
scope afterward. Nested `use_config` scopes in an execution share its connections.
Standalone queries in the same async task can also reuse a live result's owner.
Child tasks receive independent connection pools, even when they inherit config.

Each root general/asset/result query starts a fresh read-only transaction so it
can see newly published tables. Wrapper `.query()` chains use the current
transaction without refreshing. Results are lazy: another root query sharing
the owner can make retained results see newer rows or fail after a schema change.
They are not independent frozen snapshots. A failed DuckDB query can abort the
transaction; the next root query refreshes it without caller cleanup.

Results and their derived chains remain usable after leaving a config scope.
The connection cache holds weak references: wrappers and native relations keep
connections alive, and DuckDB releases the handle when the last native reference
is reclaimed. Keeping only `.relation` preserves native use but not membership
in the wrapper connection cache. Retain the wrapper while composing new root
queries with it. A join across different owning connections raises a clear error;
Assquack does not silently copy/import data or implement cross-database queries.

Source writes are rejected by the read-only transaction, and wrapper queries
accept only one SELECT. Native `.relation.query()` / `.create_view()` can attempt
persistent view creation and are unsuitable on these read-only results. This is
trusted developer SQL, not an untrusted-SQL sandbox. Do not manually close the
native relation's connection or change its transaction while using the wrapper.
External connections must use compatible DuckDB settings; other-process writers
remain subject to DuckDB's file locking rules.

The async root API and synchronous wrapper/native operations run DuckDB work on
the calling thread. Materializing native output uses a temporary transport file
during fragment capture; ordinary queries do not create that transport file.
See [the lifecycle](materialization-lifecycle.md#fragment-capture).

See [general query examples](developer-examples.md#general-database-queries).
Table globbing remains deferred, with full DuckDB file-glob-style wildcard
matching required when considered, not prefix-only matching.

### Parked cross-database query

`assquack.cross_database_query()` is a potential future feature, excluded from
MVP. Its motivating use case is joining data in multiple accessible local
Assquack databases, possibly belonging to different configs in one repository.
Its signature, source settings and result lifetime are not yet specified; the
function is not currently exported.

An alternative is for developers to own a DuckDB connection and explicitly
`ATTACH` the required databases, then issue their joins on that connection.
File accessibility, DuckDB locking and connection lifetime remain relevant.
The future Assquack API should be justified against that existing alternative;
multiple supported asset configs alone do not require automatic cross-database
query management. Reading independently published lake exports is a separate
integration use case.

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
- DuckDB relations and `AssquackQueryResult` wrappers;
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
