# Assquack Developer Examples

Assquack should feel like simple Python data assets first and DuckDB storage
machinery second. The common case is a single decorator argument and a function.

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

Names, tables, and modes are escape hatches. They are optional.

```python
@asset(
    "bronze/salesforce/opportunities.parquet",
    table="bronze.salesforce_opportunities",
)
async def opportunities(customer: str):
    yield rows
```

```python
@asset(
    export="silver/opportunities_current.parquet",
    name="salesforce.opportunities.current",
    mode="append",
)
async def opportunity_changes():
    yield changes
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
@asset("raw/vendor/events.parquet", payload="variant")
async def vendor_events():
    yield api_pages()
```

`payload="variant"` stores volatile nested source payloads in `_qa_payload
VARIANT`, while stable promoted fields can still become typed DuckDB columns.
