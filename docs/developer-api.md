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

The target package name is `assquack`.

The core package must not depend on Prefect or any other orchestrator. Runtime
adapters can call Assquack later, but the public API should work in ordinary
Python processes, notebooks, tests, CLIs, and orchestration frameworks without
changing asset definitions.

## `@asset`

`@asset` decorates a Python function and returns an `AssquackAsset` definition.
Calling the asset materializes it and returns an `AssquackResult`.

Minimal shape:

```python
@asset("bronze/orders.parquet")
async def orders():
    ...
```

The first positional argument is optional and, when present, is an export alias.
It is not the DuckDB database path and does not control where the mutable
`.duckdb` file lives.

Typed contract shape:

```python
from typing import Literal, TypedDict


AssetMode = Literal["replace"]
ExportFormat = Literal["parquet", "json", "csv"]


class ExportSpec(TypedDict):
    uri: str
    format: ExportFormat

asset(
    export: str | ExportSpec | None = None,
    *,
    name: str | None = None,
    table: str | None = None,
    mode: AssetMode = "replace",
)
```

The implementation may add narrowly scoped options over time, but database
placement must remain configuration-level rather than a decorator argument.

## Export Aliases

The positional string and the `export=` keyword describe the same export target.
These two forms are equivalent:

```python
@asset("bronze/orders.parquet")
@asset(export="bronze/orders.parquet")
```

A path-like export alias resolves to a configured export URI. For legacy
compatibility, a plain relative path conventionally maps to `mad://...`; for
example, `bronze/orders.parquet` resolves as if the developer had written
`mad://bronze/orders.parquet`.

`export=` should be used when readability matters or when no positional
argument is desired. It must not conflict with a positional export alias. If
both are supplied, the implementation should reject ambiguous input.

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

Export aliases can seed the default asset name when `name=` is omitted, but the
resolved export destination is not part of the canonical identity. Changing an
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

Assquack infers the asset name, table, and materialization mode by convention.
Developers can override them when needed:

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
- `.export_parquet()` to write a Parquet compatibility export.
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

## Cache First

`.cache_first()` returns an asset callable that prefers the latest valid cached
materialization. If metadata says the cache is valid and the current table still
exists, the callable returns an `AssquackResult` without rematerializing the
wrapped function.

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

Asset functions may return or yield batches. Supported inputs should remain
broad and framework-agnostic:

- Python mappings and lists of mappings;
- sync and async iterables;
- pandas DataFrames;
- Arrow Tables and RecordBatches;
- DuckDB relations;
- `httpx.Response` values.

Assquack treats these values as observed data for the current run. The
materializer normalizes them into DuckDB staging tables, records schema
evidence, and updates the current table according to the asset's mode.

## Related Docs

- [Developer Examples](developer-examples.md): short usage examples for this
  API contract.
- [Materialization Lifecycle](materialization-lifecycle.md): what happens when
  an asset is called.
- [Exports](exports.md): how positional paths and file type aliases become
  compatibility artifacts.
- [Configuration](configuration.md): where database paths and export bases are
  configured.
- [Storage Model](storage-model.md): the tables and locks behind asset results.
