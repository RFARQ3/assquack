# MAD.Prefect Reference

This document captures the MAD.Prefect data asset behavior that is useful for
Assquack planning. It is reference material only: Assquack must not import,
vendor, or depend on Prefect or `mad_prefect` at runtime.

Assquack should keep the good developer ergonomics from MAD.Prefect's data asset
API while replacing the Prefect-bound, file-fragment-first implementation with a
DuckDB-native storage model.

## Sources Inspected

- `.submodules/MAD.Prefect/README.md`
- `.submodules/MAD.Prefect/docs/asset-manifest.md`
- `.submodules/MAD.Prefect/docs/data-asset-cache-first.md`
- `.submodules/MAD.Prefect/docs/data-asset-mad-protocol.md`
- `.submodules/MAD.Prefect/docs/data-asset-attribute-formatting.md`
- `.submodules/MAD.Prefect/mad_prefect/data_assets/asset_decorator.py`
- `.submodules/MAD.Prefect/mad_prefect/data_assets/data_asset.py`
- `.submodules/MAD.Prefect/mad_prefect/data_assets/data_asset_callable.py`
- `.submodules/MAD.Prefect/mad_prefect/data_assets/data_artifact.py`
- `.submodules/MAD.Prefect/mad_prefect/data_assets/data_artifact_collector.py`
- `.submodules/MAD.Prefect/mad_prefect/data_assets/data_artifact_query.py`
- `.submodules/MAD.Prefect/mad_prefect/data_assets/asset_metadata.py`
- `.submodules/MAD.Prefect/mad_prefect/data_assets/data_asset_run.py`
- `.submodules/MAD.Prefect/mad_prefect/data_assets/configurators/fluent_data_asset_configurator.py`
- `.submodules/MAD.Prefect/mad_prefect/duckdb.py`
- `.submodules/MAD.Prefect/mad_prefect/filesystems.py`

## What MAD.Prefect Data Assets Do Today

MAD.Prefect exposes a path-first `@asset(...)` decorator. The decorator captures
a result path, optional artifact directory, optional name, snapshot flag,
intermediate artifact file type, DuckDB read options, and cache TTL. It returns a
`DataAsset` object instead of the original function.

The `DataAsset` object provides:

- `with_arguments(*args, **kwargs)` to derive an asset with arguments bound.
- `with_options(...)` to derive an asset with updated path, name, artifact, read,
  snapshot, or cache settings. This is reference behavior, not part of the
  Assquack MVP API.
- `cache_first(expiration=None)` to prefer an existing materialization by setting
  a long default TTL.
- `query(query_str=None, params=None)` to materialize or reuse the asset, then
  query the resulting artifact with DuckDB.

Asset names, paths, and artifact directories can contain Python format-style
placeholders. Argument-bound assets partially format the placeholders they know;
execution performs strict formatting before materialization. Asset names are
sanitized and an asset id is derived from name, path, artifact directory, and
bound arguments.

The current execution lifecycle is:

1. Resolve any call-time arguments into a concrete asset instance.
2. Create a `DataAssetRun` with run id, asset id, asset name, path, and runtime.
3. Determine one or more final result artifact paths from the asset path suffix.
   A suffix such as `.parquet|csv` produces multiple final outputs.
4. Load the latest manifest from `_asset_metadata/asset_name=<name>/asset_id=<id>/manifest.json`.
5. If the cache TTL is valid and the final result artifact exists, return the
   existing artifact.
6. Persist preliminary run metadata with status `unknown`.
7. Delete the previous intermediate artifact directory unless
   `snapshot_artifacts` is enabled.
8. Register the custom DuckDB `mad://` filesystem.
9. Execute the user function and collect returned or yielded batches into
   intermediate fragment artifacts.
10. Query the fragment artifacts through DuckDB.
11. Persist the merged DuckDB result into the final result artifact or artifacts.
12. Persist successful run metadata and update the manifest.

The storage layer is `FsspecFileSystem`, configured by `FILESYSTEM_URL` or a
Prefect filesystem block. The custom `mad://` DuckDB filesystem is a
`DirFileSystem` wrapper over that fsspec base path, so DuckDB can read paths such
as `mad://bronze/orders.parquet` while Python writes through fsspec.

The metadata model is filesystem-backed JSON. Each run writes
`_asset_metadata/.../asset_run_id=<run_id>/metadata.json`, and the manifest keeps
the most recent status, materialization timestamp, run metadata path, and output
artifact paths. Older metadata lookup can still glob JSON run metadata and query
it through DuckDB.

## Accepted Inputs And Output Handling

MAD.Prefect is intentionally flexible about the values an asset function can
return or yield. Useful supported shapes include:

- Python dicts and lists.
- Sync and async generators.
- pandas DataFrames.
- PyArrow tables and record batches.
- DuckDB relations.
- `httpx.Response`, interpreted via `.json()`.
- Existing `DataArtifact` values.
- Nested `DataAsset` values, which are materialized and queried before being
  written into the parent artifact.

`DataArtifact` persists the normalized batches as JSON lines, Parquet, or CSV.
DuckDB relations are streamed as Arrow record batches or copied directly to the
registered fsspec filesystem. Parquet writes attempt permissive schema unification
as later batches introduce new columns.

`DataArtifactQuery` reads persisted artifacts back into DuckDB:

- JSON uses `read_json(...)` with configurable `ReadJsonOptions`.
- Parquet uses `read_parquet(...)` with `hive_partitioning=true` and
  `union_by_name=true`.
- CSV uses `read_csv(...)` with configurable `ReadCSVOptions`.

The query helper treats the file collection as `artifact_query`, then applies the
caller's SQL fragment on top.

## Useful Ergonomics To Preserve

Assquack should preserve the parts that make MAD.Prefect assets pleasant to use:

- A path-first decorator where `@asset("bronze/orders.parquet")` is enough for a
  simple asset.
- A decorated object with direct execution and `.query(...)`.
- `cache_first(...)` as a readable way to express "reuse the latest successful
  materialization if it is still valid."
- Argument-bound assets via `.with_arguments(...)`.
- Option-bound derivatives via `.with_options(...)` as a future or adapter-only
  compatibility feature, not an MVP Assquack API requirement.
- Template formatting for path, name, and export target values.
- Broad input normalization across Python objects, generators, pandas, Arrow,
  DuckDB relations, and HTTP responses. Nested asset materialization is useful
  reference behavior for later dependency handling, not an MVP input contract.
- DuckDB SQL as the normal query interface for materialized data.
- Manifest-style latest-run lookup so cache checks do not need expensive storage
  scans.
- Multiple export formats from a single materialization where that is useful for
  compatibility.

These are API and workflow conveniences, not implementation constraints.

## Pain Points In The Current Design

MAD.Prefect makes files the durable evidence for materialization. Each batch is
persisted as an intermediate JSON, Parquet, or CSV fragment; DuckDB then reads
the fragments and writes final result files. This causes several practical
problems that Assquack should avoid.

- Raw JSON fragments can become much larger than the final Parquet result because
  they are retained as schema inference and replay input.
- DuckDB is mostly a query and merge engine over files, not the durable asset
  store.
- Cache validity means "manifest says recent and result artifact exists"; it is
  not tied to transactional table state.
- Snapshotting retains filesystem fragments by runtime partition. It does not
  provide table versions or explicit current/history semantics.
- Incremental behavior is implicit. Replace, append, merge, delta, and snapshot
  modes are not first-class materialization modes.
- Schema evolution is split between PyArrow write logic, DuckDB file readers, and
  caller-provided read options.
- Querying depends on artifact file types. Mixed file types cannot be queried as
  one artifact collection.
- Metadata lives outside the query store as JSON files, so run state and asset
  data are not transactionally updated together.
- The storage abstraction is coupled to Prefect filesystem/deployment block
  types, even though the data asset pattern itself is useful outside Prefect.
- The `mad://` protocol is useful for compatibility, but it is another adapter
  layer between DuckDB and the actual fsspec storage.

## What Assquack Changes

Assquack keeps the asset pattern but changes the storage center of gravity:
materialized data should live first in DuckDB tables, with exported files treated
as compatibility outputs.

The key changes are:

- No core Prefect dependency.
- No requirement to persist full raw JSON or intermediate file fragments before
  DuckDB can infer or query data.
- Use an embedded local `.duckdb` database as the MVP write target.
- Store asset registry, runs, schemas, observations, and exports in DuckDB system
  tables instead of filesystem JSON metadata.
- Materialize through staging tables and transactionally replace the current
  table for MVP `replace` mode.
- Treat yielded batches as observed data. Infer and update schema evidence from
  the rows received in each run.
- Store volatile nested payloads as DuckDB `VARIANT` by default, with typed
  columns promoted over time.
- Make materialization modes explicit: start with `replace`, then add `append`,
  `merge`, and snapshot/history semantics deliberately.
- Keep ADLS, Parquet, CSV, and `mad://` style paths as export or compatibility
  concerns rather than the primary storage model.

In practice, an Assquack asset should still feel familiar:

```python
from assquack import asset

@asset("bronze/orders.parquet")
async def orders():
    yield [{"id": 1, "status": "open"}]

result = await orders.cache_first().query("SELECT * FROM data")
```

But the runtime should insert batches into DuckDB staging, update Assquack
metadata tables, replace or update the current asset table, and optionally export
Parquet after the table state is committed.

## Optional Legacy Adapter Direction

If existing MAD.Prefect projects need a migration bridge, build it as a separate
optional adapter package. The dependency direction must be:

```text
mad-prefect-assquack-adapter -> assquack
mad-prefect-assquack-adapter -> mad_prefect / prefect
assquack -> no Prefect dependency
```

Possible adapter responsibilities:

- Wrap an Assquack asset in a MAD.Prefect-like object exposing familiar
  `with_arguments`, `with_options`, `cache_first`, and `query` methods.
- Map legacy `path` values to Assquack export aliases.
- Map `cache_expiration` to Assquack metadata-table cache TTL checks.
- Translate `snapshot_artifacts` to Assquack snapshot/history mode once that mode
  exists, or to export snapshot retention for interim compatibility.
- Export current Assquack tables to Parquet/CSV/JSON paths expected by existing
  file-backed consumers.
- Register or emulate `mad://` only for legacy read/write compatibility.
- Read existing `_asset_metadata` manifests as migration hints, not as Assquack's
  source of truth.

The adapter should be thin. It should not copy the MAD.Prefect materialization
engine into Assquack, and it should not reintroduce file fragments as the primary
runtime storage path.

## Related Docs

- [Overview](overview.md): Assquack identity and non-goals.
- [Developer API](developer-api.md): the standalone API that preserves useful
  ergonomics without a Prefect dependency.
- [Materialization Lifecycle](materialization-lifecycle.md): how Assquack
  replaces file-fragment-first materialization.
- [Schema Inference](schema-inference.md): how continuous inference moves from
  raw files into DuckDB metadata.
- [Roadmap](roadmap.md): when optional legacy adapter work should be considered.
