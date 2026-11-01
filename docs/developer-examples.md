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

Read one configured database and consume its connection-owning query result:

```python
import assquack
from assquack import asset


@asset("silver.weekly_history", config=namely)
async def weekly_history():
    return await assquack.query("SELECT * FROM history.report_snapshots")


# Outside an asset, process environment configuration selects the database.
history = await assquack.query("SELECT * FROM history.report_snapshots")
```

Here `namely` is an `AssquackConfig`. Select it explicitly outside an asset
without changing process environment variables:

```python
with assquack.use_config(namely):
    history = await assquack.query(
        "SELECT * FROM history.report_snapshots WHERE report_id = ?", [42]
    )

rows = history.fetchall()
```

Use the connection-owning result to query Python variables directly:

```python
with assquack.use_config(namely):
    reports = await assquack.query("SELECT * FROM bronze.reports")
    latest = reports.query(
        "SELECT * FROM reports WHERE snapshot_date = CURRENT_DATE"
    )
    summary = latest.query("SELECT COUNT(*) FROM latest")

rows = summary.fetchall()
```

`reports = await reports_asset.query()` supports the same chain. Two results
read from the same database in one task/execution can be joined:

```python
with assquack.use_config(namely):
    reports = await reports_asset.query()
    people = await people_asset.query()
    joined = reports.query(
        "SELECT r.*, p.display_name FROM reports r "
        "JOIN people p ON r.person_id = p.id"
    )
```

These assets must select the same database. Different task/execution connections
cannot be joined implicitly. The wrapper does not change `duckdb.query` or its
module default. Use `joined.relation` when native relational methods are needed.

The result needs no caller-managed connection cleanup. The explicit scope selects
query reads only, including reads inside nested assets; publishing still uses each
asset's effective config. See the [query contract](developer-api.md#general-database-queries)
for lifetime and the parked `assquack.cross_database_query()` feature.
