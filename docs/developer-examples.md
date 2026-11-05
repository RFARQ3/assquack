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

The export target can be a path or a file type alias. In the sketches below,
`rows` stands for records supplied by your source.

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
This sketch uses `rows` for records supplied by your source.

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

Assquack supplies the result's fully qualified source table. See the
[query contract](developer-api.md#from-first-result-queries).

```python
artifact = await orders()
# No prior orders() call is needed: query() materializes the asset using its cache policy.
relation = await orders.query("SELECT count(*)")
cached = orders.cache_first()
```

Bound arguments also resolve table templates. Each customer writes to its own
table; this example writes to `sales.orders_acme`:

```python
from assquack import asset


@asset("sales.orders_{customer}")
def customer_orders(customer: str):
    return [{"customer": customer, "amount": 125.0}]


acme_orders = customer_orders.with_arguments(customer="acme")
rows = await acme_orders.query("SELECT * WHERE amount > 100")
```

## Composing queries

Both avenues below produce the same totals. Assume `orders` contains
`order_date`, `customer_id` and `amount`. First select January's orders:

```python
january_orders = await orders.query("""
    SELECT *
    WHERE order_date >= DATE '2026-01-01'
      AND order_date < DATE '2026-02-01'
""")
```

### SQL references the Python variable

```python
import duckdb

totals = duckdb.query("""
    SELECT customer_id, sum(amount) AS total
    FROM january_orders
    GROUP BY customer_id
""")
print(totals.fetchall())
```

DuckDB looks up `january_orders` by its Python variable name; no alias argument
is needed. A linter sees only a string, so if that is the variable's only use,
it can report it as unused. Ruff's `F841` applies to local variables inside
functions; it does not flag module-level assignments. Other tools may differ.
Developers choosing this avenue can decide how to handle their lint warning.

### Python calls the relation directly

```python
totals = january_orders.query(
    "january_orders",
    """
    SELECT customer_id, sum(amount) AS total
    FROM january_orders
    GROUP BY customer_id
    """,
)
print(totals.fetchall())
```

The method call visibly uses the Python variable, so that assignment does not
trigger an unused-variable warning. DuckDB requires the extra alias argument;
the SQL uses that alias. It need not match the Python variable name, but matching
it here keeps the example easy to follow. This is native `DuckDBPyRelation.query`,
not Assquack's asset/result method.

Choosing between these avenues belongs to the downstream developer. Assquack
requires neither lint suppression nor an extra wrapper. Both compose lazy
relations; the `fetchall()` calls execute them. For a leading `WITH` statement,
use either native avenue to build a complete SQL query over the initial relation.

References: [DuckDB relations](https://duckdb.org/docs/1.5/clients/python/relational_api),
[Ruff unused variables](https://docs.astral.sh/ruff/rules/unused-variable/).

## Semi-Structured Payloads

This sketch uses `api_pages()` for your source-specific API reader.

```python
@asset(table="raw.events", export="raw/vendor/events.parquet")
async def vendor_events():
    yield api_pages()
```

Assquack writes the returned API rows into bounded local NDJSON fragments, then
uses DuckDB's native JSON reader to infer the combined shape and build typed
columns. Genuinely heterogeneous fields can remain `VARIANT`. Fragment placement
and sizing are runtime configuration, not decorator arguments.

## Manual Export Destinations

```python
from assquack import FileSystemDestination

result = await orders()
result.export("inspection/orders.csv|json")
result.export(FileSystemDestination(base_uri="./reports", path="orders", format="csv"))
result.export("abfss://account.dfs.core.windows.net/lake/orders.parquet")
```

Strings use automatic authentication; local paths need no credentials. Azure
requires the configured environment inputs described in
[ADLS credentials](configuration.md#adls-credentials). Objects supply their own
settings. See [manual exports](exports.md#manual-exports) for defaults.

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

Configure public catalog names and let assets attach on first use:

```python
from pathlib import Path
import assquack
import duckdb

people = assquack.AssquackConfig(home=Path("./people"), attachment_name="people")
work = assquack.AssquackConfig(home=Path("./work"), attachment_name="work")

@assquack.asset("hr.employees", config=people)
def employee_asset():
    return [{"id": 1, "department_id": 3}]

@assquack.asset("hr.departments", config=work)
def department_asset():
    return [{"id": 3, "name": "Engineering"}]

await employee_asset()
await department_asset()
employees = await employee_asset.query()
departments = await department_asset.query()
joined = duckdb.query(
    "SELECT e.id, d.name FROM employees e "
    "JOIN departments d ON e.department_id = d.id"
)
print(joined.fetchall())
```

For an existing database without an asset invocation, attach explicitly:

```python
people.attach()
rows = duckdb.query("SELECT id FROM people.hr.employees WHERE id = ?", params=[1])

# people was attached first, so it remains the default.
same_rows = duckdb.query("SELECT id FROM hr.employees WHERE id = ?", params=[1])
```

Both queries return native relations. DuckDB resolves short names through its
default database and search path, even with multiple attachments. See the
[query contract](developer-api.md#general-database-queries) for lazy-result lifetime.
