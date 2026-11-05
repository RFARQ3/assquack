# From a Prefect flow to DuckDB tables and Azure exports

This walkthrough follows a fictional `MAD.Data.Sales` integration with the
sales.com API. It uses module-level bound assets, a concrete derived asset and
a plain Prefect `@flow`. Prefect and Kubernetes belong to the downstream project;
Assquack itself depends on neither.

## 1. Prefect starts the Kubernetes pod

The deployment image already contains the application and its Python dependencies,
including Prefect, Assquack and DuckDB. The pod mounts storage at `/data` and
receives these process environment variables before Python starts:

```text
ASSQUACK_HOME=/data/sales
ASSQUACK_DATABASE_PATH=/data/sales/sales.duckdb
ASSQUACK_ATTACHMENT_NAME=sales

ASSQUACK_EXPORTS=abfss://exampleaccount.dfs.core.windows.net/lake/sales
AZURE_TENANT_ID=<tenant for the pod's workload identity>
AZURE_CLIENT_ID=<client ID for the pod's workload identity>
AZURE_FEDERATED_TOKEN_FILE=<path to the pod's mounted identity token>
```

The deployment configures the pod's Azure workload identity, supplies these
identity environment variables and mounts its federated token file. That identity
has permission to write the export files. A plain `ASSQUACK_EXPORTS` string uses
`auth="auto"`: the Azure credential chain uses the configured workload identity.
Assquack does not create the identity or grant storage permissions.

With `auto`, this complete workload-identity configuration takes priority over
SAS. If chain configuration is absent or incomplete, `auto` can instead select
SAS when both `AZURE_STORAGE_ACCOUNT_NAME` and `AZURE_STORAGE_SAS_TOKEN` are
present. This pod example needs no SAS token. A destination can explicitly select
`auth="credential_chain"` or `auth="sas"`; missing inputs fail rather than
switching methods. Once selected, authentication failure also propagates without
trying another strategy. Assquack reads the process environment, not a `.env` file.
See [configuration](configuration.md#adls-credentials) for authentication options.

## 2. The downstream application defines its assets

The imported API and input helpers below are illustrative application functions.
`paginate_api` owns the HTTP client internally. The file readers return an existing
pandas DataFrame or PyArrow Table; their loading details are outside Assquack.

```python
import asyncio
from datetime import timedelta

from assquack import asset
from prefect import flow

from mad_data_sales.core.api import paginate_api, get_api
from mad_data_sales.core.inputs import (
    read_targets_dataframe,
    read_catalogue_arrow_table,
)


@asset(
    table="bronze.{endpoint}",
    export="bronze/{endpoint}.parquet|json",
    cache_expiration=timedelta(hours=2),
)
async def paginated_asset(endpoint: str):
    async for page in paginate_api(endpoint):
        yield page  # A list of dictionaries.


customers = paginated_asset.with_arguments(endpoint="customers")
orders = paginated_asset.with_arguments(endpoint="orders")


@asset(
    table="bronze.account_details",
    export="bronze/account_details.json",
)
async def account_details():
    return await get_api("account")  # One dictionary representing one row.


@asset(
    table="bronze.sales_targets",
    export="bronze/sales_targets.csv",
)
async def sales_targets():
    return read_targets_dataframe()  # pandas DataFrame.


@asset(
    table="bronze.product_catalogue",
    export="bronze/product_catalogue.parquet",
)
async def product_catalogue():
    return read_catalogue_arrow_table()  # PyArrow Table.


@asset(
    table="silver.order_totals",
    export="silver/order_totals.csv|json",
)
async def order_totals():
    return await orders.query("""
        SELECT customer_id, sum(amount) AS total
        GROUP BY customer_id
    """)  # DuckDBPyRelation.


@flow
async def materialize_sales_assets():
    results = await asyncio.gather(
        customers(),
        account_details(),
        sales_targets(),
        product_catalogue(),
        order_totals(),
        return_exceptions=True,
    )

    exceptions = [r for r in results if isinstance(r, Exception)]
    if exceptions:
        raise ExceptionGroup("Sales asset materialization failed", exceptions)
```

These definitions produce six assets covering five input types. `orders` runs
through `order_totals` calling `orders.query()`; it needs no separate flow call.

## 3. Import initializes the database; decorators define the assets

When Python imports Assquack, [__init__.py](../src/assquack/__init__.py) detects
the placement environment variables. [AssquackConfig](../src/assquack/_config.py)
loads the settings and calls `.attach()`.

[database.py](../src/assquack/_storage/database.py) opens or creates
`/data/sales/sales.duckdb` on DuckDB's shared default connection, attaches it as
`sales`, applies engine settings and selects the first Assquack attachment as
the default database. [tables.py](../src/assquack/_storage/tables.py) initializes
the `assquack_meta` schema and metadata tables.

The decorators create `AssquackAsset` definitions. `.with_arguments(...)` creates
bound definitions at module level. Neither definition nor binding fetches API data.

## 4. Calling an asset creates a MaterializationRequest

Prefect executes the flow, which calls the globally defined assets.
[AssquackAsset.__call__()](../src/assquack/_api.py) selects the execution config
and calls `self._request(args, kwargs)`.

`_request()` combines bound and supplied arguments, applies function defaults,
and uses `AssetTemplateFormatter` to resolve table, name and export templates.
For `orders`, the table is `bronze.orders` and the export declaration is
`bronze/orders.parquet|json`. It also calculates the asset ID from the function,
resolved name and arguments.

It creates a [MaterializationRequest](../src/assquack/_materialization/models.py)
containing the producer function, arguments, asset identity, table reference,
export declaration, materialization mode and cache policy. `__call__()` passes
that request and the config to `materialize()` in
[pipeline.py](../src/assquack/_materialization/pipeline.py).

## 5. The pipeline checks whether extraction is needed

The pipeline resolves export destinations and qualifies the request's table with
its configured database. During resolution, the authentication registry loads
credential inputs once and checks each strategy's registered input combinations.
The complete pod workload-identity inputs select the credential chain. Assquack
fingerprints the selected authentication configuration without storing raw
credentials in metadata; DuckDB obtains the runtime token when exporting.

`MetadataRepository` records the asset definition in
`assquack_meta.assets`, recovers interrupted run records and prunes obsolete
fragments.

If caching is enabled, [_cache.py](../src/assquack/_cache.py) checks the latest
successful run's age and whether its table exists. With a valid two-hour cache,
the pipeline skips extraction, attempts any exports not already recorded as
complete for that run and destination, and returns an `AssquackResult`.

Otherwise it records a new run in `assquack_meta.runs` and invokes the producer.
Follow a fresh orders extraction through the next steps.

## 6. The producer's output becomes local NDJSON fragments

`paginate_api("orders")` fetches pages internally and yields lists of dictionaries.
[write_fragments()](../src/assquack/_materialization/fragments.py) consumes each
yielded value and selects the matching write path:

| Asset output | How it is written |
| --- | --- |
| List of dictionaries (`orders`, `customers`) | Iterate the list; `NDJSONFragmentWriter.write_rows()` serializes each dictionary with Python `json.dumps()`, adds a newline and writes UTF-8 bytes. |
| Dictionary (`account_details`) | The same Python writer writes the dictionary as one row. It does not unpack a response envelope automatically. |
| `DuckDBPyRelation` (`order_totals`) | `write_relation()` uses DuckDB `COPY ... FORMAT JSON` with `ARRAY false`. The lazy query executes here and DuckDB writes NDJSON directly. |
| pandas DataFrame (`sales_targets`) | `connection.from_df()` creates a relation, then `write_relation()` uses DuckDB's JSON writer. |
| PyArrow Table (`product_catalogue`) | `connection.from_arrow()` creates a relation, then the same DuckDB JSON writer handles it. |

DataFrames and Arrow tables are not converted into Python lists of dictionaries.
For native relations, the writer supplies a generated Python variable name for
DuckDB's replacement scan, then runs COPY on the shared connection. It counts
rows in the written files and removes empty outputs.

Files live beneath `/data/sales/fragments`, separated by asset and run. Python
writing rolls over after the configured size target is reached without splitting
a row; DuckDB COPY uses its file-size target. Files start as `.partial` and are
renamed to `fragment=000000.ndjson` and subsequent names when complete.
`finish()` creates the `_COMPLETE` marker after extraction finishes.

The pipeline holds no database connection lock across awaited API calls. Native
DuckDB writes use `CONNECTION_LOCK`. NDJSON is the internal capture format;
the asset's export declaration independently chooses its Azure output formats.

## 7. Assquack builds the staging table

[schema.py](../src/assquack/_materialization/schema.py) uses DuckDB to infer the
current extraction's columns and types. [json_reader.py](../src/assquack/_materialization/json_reader.py)
supplies the reads; for the relevant oversized-record failure, `JsonReadRecovery`
measures the required record size and retries with an adjusted reader limit.

[staging.py](../src/assquack/_materialization/staging.py) builds the candidate
table in `assquack_stage`, using `VARIANT` for fields inferred as JSON and adding
`_qa_run_id` and `_qa_loaded_at`. The existing published table is still unchanged.

## 8. Assquack publishes the table

[commit.py](../src/assquack/_materialization/commit.py) replaces
`sales.bronze.orders` from the staging table and updates successful publication
metadata in one transaction. Failure rolls back that transaction.

After publication, the pipeline drops staging and
[retention.py](../src/assquack/_materialization/retention.py) prunes older diagnostic
fragments according to its retention rules. Selected fragments remain available
for diagnosis.

## 9. Assquack exports the published table to Azure Data Lake

[targets.py](../src/assquack/_exports/targets.py) interprets suffixes and pipe
declarations. A pipe requests multiple output formats, not a transformation pipeline.
Under the configured Azure export base, the assets produce:

| Asset | Export files |
| --- | --- |
| `customers` | `bronze/customers.parquet` and `bronze/customers.json` |
| `orders` | `bronze/orders.parquet` and `bronze/orders.json` |
| `account_details` | `bronze/account_details.json` |
| `sales_targets` | `bronze/sales_targets.csv` |
| `product_catalogue` | `bronze/product_catalogue.parquet` |
| `order_totals` | `silver/order_totals.csv` and `silver/order_totals.json` |

[operations.py](../src/assquack/_exports/operations.py) coordinates writes through
`FileSystemDestination`. Azure preparation and authentication configure DuckDB
access through the pod's workload identity. `DuckDBFileWriter` exports each format
from the published table, and each successful output is recorded in
`assquack_meta.exports`. Automatic exports and `result.export(destination)` share
this write operation.

## 10. The derived asset completes and the flow returns

For `order_totals`, its call to `orders.query()` first completes the orders
materialization/cache path above. The result query supplies
`FROM "sales"."bronze"."orders"` before the aggregation SQL and returns a native
lazy `DuckDBPyRelation`. No caller-supplied SQL is parsed or rewritten.

`order_totals` returns that relation to its own materialization pipeline. DuckDB
executes the aggregation when writing its JSON fragments; the same staging,
publication and export steps produce `sales.silver.order_totals` and its CSV/JSON
outputs. The other flow branches follow their respective input paths concurrently;
synchronous database operations are protected by the shared connection lock.

Each successful asset call returns an `AssquackResult` with its table reference
and run information. The flow waits for all its calls and raises collected errors
to Prefect. An export failure propagates, but does not undo the already-committed
DuckDB publication. The database remains on mounted storage; reuse by a later pod
depends on that mount's persistence.

## Related Docs

- [Developer API](developer-api.md): asset, result and query contracts.
- [Materialization lifecycle](materialization-lifecycle.md): detailed execution rules.
- [Configuration](configuration.md): environment variables and Azure authentication.
- [Exports](exports.md): formats, destinations and manual exports.
