# Assquack Developer Examples

Assquack should feel like simple Python data assets first and DuckDB storage
machinery second. The common case is a single decorator argument and a function.

With no configuration, Assquack uses the process current working directory as
its home. Starting a local script from a repository root therefore stores the
default development database at `./dev/assquack.duckdb`. Set `ASSQUACK_HOME`
when the database, temporary files, and local exports should live elsewhere.

## Legacy-Style Asset

```python
from assquack import asset


@asset("this/is/a/legacy/asset.parquet")
def legacy_asset():
    return [{"id": 1, "value": "hello"}]
```

The positional string is an export alias. The example above is equivalent to:

```python
@asset(export="mad://this/is/a/legacy/asset.parquet")
def legacy_asset():
    return [{"id": 1, "value": "hello"}]
```

Assquack infers the internal table, asset name, and materialization mode from
convention. The default mode is `replace`.

## File Type Aliases

The export target can be a path or a file type alias.

```python
@asset("bronze/orders.parquet")
def orders():
    return rows


@asset("bronze/orders.parquet|json")
def orders_with_legacy_json_export():
    return rows


@asset(export="parquet")
def customer_summary():
    return rows
```

`"parquet"` means "export this asset to the configured default export base as
Parquet". The concrete path is derived from the asset name unless explicitly
provided.

## Optional Overrides

Names, tables, and modes are escape hatches. They are optional. In the MVP,
`mode` defaults to `"replace"` and no other mode is part of the public contract.

```python
@asset(
    "bronze/salesforce/opportunities.parquet",
    table="bronze.salesforce_opportunities",
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
@asset("raw/vendor/events.parquet")
async def vendor_events():
    yield api_pages()
```

Assquack stores volatile nested source payloads in raw staging as `_qa_payload
VARIANT`, while stable promoted fields can still become typed DuckDB columns.
That behavior is part of materialization and schema inference, not a decorator
argument.

## Related Docs

- [Developer API](developer-api.md): the full public API contract behind these
  examples.
- [Configuration](configuration.md): where database placement and export
  defaults are configured.
- [Exports](exports.md): export alias and file type behavior.
- [Materialization Lifecycle](materialization-lifecycle.md): how returned or
  yielded rows become DuckDB tables.
- [Schema Inference](schema-inference.md): why `_qa_payload VARIANT` is useful
  for inconsistent API payloads.
