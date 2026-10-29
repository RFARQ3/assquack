# Assquack Developer Examples

The examples below follow the [table-first decision](journal/2026-09-08-mvp-scope.md#table-first-decorator-decision-27-october-2026),
with the required-table clarification of 28 October 2026, now implemented.

## Table-first asset

```python
from assquack import asset


@asset(
    table="sales.orders",
    mode="replace",
    name="sales_orders",
    export="sales/orders.parquet",
)
def orders():
    return [{"order_id": 1, "amount": 125.0}]
```

The table may also be positional: `@asset("sales.orders")`. Omitting `export`
leaves a DuckDB-only asset. Keyword arguments make each setting explicit.

## Zero-Configuration Local Storage

Calling an asset requires no database configuration during local development:

```python
result = await orders()
```

Assquack uses the current working directory as its default home and creates the
DuckDB database at `./assquack.duckdb`. `ASSQUACK_HOME` or
`AssquackConfig` can override that location for deployments.

## File Type Aliases

The export target can be a path or a file type alias.

```python
@asset(table="bronze.orders", export="bronze/orders.parquet")
def orders():
    return rows


@asset(table="bronze.orders", export="bronze/orders.parquet|json")
def orders_with_legacy_json_export():
    return rows


@asset("main.customer_summary", export="parquet")
def customer_summary():
    return rows
```

`"parquet"` means "export this asset to the configured default export base as
Parquet". The concrete path is derived from the asset name unless explicitly
provided.

## Optional Overrides

Table is required. Name, mode, and export are optional. In the MVP,
`mode` defaults to `"replace"` and no other mode is part of the public contract.

```python
@asset(
    table="bronze.salesforce_opportunities",
    export="bronze/salesforce/opportunities.parquet",
)
async def opportunities(customer: str):
    yield rows
```

There is no per-asset database placement argument. Database placement and
locking belong to Assquack configuration and deployment.

## Querying

```python
artifact = await orders()
relation = await orders.query("SELECT count(*) FROM data")
cached = orders.cache_first()
```

Bound arguments keep path-template ergonomics:

```python
acme_orders = orders.with_arguments(customer="acme")
rows = await acme_orders.query("SELECT * FROM data WHERE amount > 100")
```

## Semi-Structured Payloads

```python
@asset(table="raw.events", export="raw/vendor/events.parquet")
async def vendor_events():
    yield api_pages()
```

Assquack writes the returned API rows into bounded local NDJSON fragments, then
uses DuckDB's native JSON reader to infer the combined shape and build typed
columns. Genuinely heterogeneous fields can remain `VARIANT`. Fragment placement
and sizing are runtime configuration, not decorator arguments.

## Related Docs

- [Developer API](developer-api.md): the full public API contract behind these
  examples.
- [Configuration](configuration.md): where database placement and export
  defaults are configured.
- [Exports](exports.md): export alias and file type behavior.
- [Materialization Lifecycle](materialization-lifecycle.md): how returned or
  yielded rows become DuckDB tables.
- [Schema Inference](schema-inference.md): how local JSON evidence becomes typed
  columns and selective `VARIANT` values.

## General database queries

Implemented MVP API examples; validation is tracked in
[Query And Cache](epics/01-mvp/03-query-and-cache.md#general-database-query-implementation).
`namely`, `space_calculator`, `openasset_config`, and `salesforce_config` below
are ordinary `AssquackConfig` objects pointing to existing local databases.

```python
import assquack
from assquack import asset


@asset("silver.weekly_history", config=namely)
async def weekly_history():
    return await assquack.query("SELECT * FROM history.report_snapshots")


# Outside an asset, ASSQUACK_DATABASE_PATH or ASSQUACK_HOME must identify
# the existing source database in the process environment.
with await assquack.query("SELECT * FROM history.report_snapshots") as history:
    rows = history.fetchall()  # Explicit Python collection, when needed.


@asset("gold.projects", config=space_calculator)
async def projects():
    return await assquack.query(
        """
        SELECT p.code, o.client_name
        FROM openasset.bronze.projects p
        JOIN salesforce.silver.opportunities o
            ON p.code = o.project_code
        """,
        sources={
            "openasset": openasset_config,
            "salesforce": salesforce_config,
        },
    )
```

Explicit sources replace the implicit read context; the output still publishes
using `space_calculator`. Source assets are not executed or refreshed. See the
[source selection contract](developer-api.md#general-database-queries).
