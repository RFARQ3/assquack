# Partial And Lazy Asset Template Formatting

Status: Working MVP requirement observation

This document records prototype behaviour and the reasoning behind it. It does
not change the contracts in the existing canonical documentation.

## Observed Requirement

An asset definition must be usable before every runtime argument is known.
Template fields in `export`, `name`, and `table` should resolve progressively as
arguments are supplied, while unresolved fields remain intact.

Strict validation belongs at the execution boundary. Materialization, querying,
and existence checks need a concrete asset identity and table, so they must fail
clearly when required arguments or template fields are still unresolved.

This permits asset factories, chained `.with_arguments(...)` calls, and assets
whose templates refer to another bound asset without materializing anything
prematurely.

## Basic Example

```python
@asset(
    "bronze/{endpoint}.parquet",
    name="api_{endpoint}",
    table="bronze.{endpoint}",
)
async def api_asset(endpoint: str, response_key: str | None = None):
    ...
```

The initial definition preserves all three templates. Binding an unrelated or
optional argument must not require `endpoint` yet:

```python
partially_bound = api_asset.with_arguments(response_key="items")
```

At this point the unresolved values remain:

```text
export = bronze/{endpoint}.parquet
name   = api_{endpoint}
table  = bronze.{endpoint}
```

A later binding resolves them:

```python
ready = partially_bound.with_arguments(endpoint="contacts")
```

```text
export = bronze/contacts.parquet
name   = api_contacts
table  = bronze.contacts
```

Chained calls merge their arguments. A later call replaces an earlier value for
the same argument, and every new asset instance renders from the original
templates rather than from the previous instance's partially rendered strings.

## Why Original Templates Must Be Retained

Formatting is lossy once a placeholder has been replaced. If an asset stored
only its current rendered strings, rebinding `endpoint="customers"` after first
binding `endpoint="contacts"` could not reconstruct the intended paths.

The prototype therefore keeps an immutable `_AssetTemplates` record containing
the declared `export`, `name`, and `table` values. Each derived asset receives
its own bound-argument mapping and renders a fresh public view from those source
templates.

The structured `ExportSpec` mapping is also copied when the source templates are
captured. This prevents one asset instance from accidentally changing the
template state used by another instance.

## Nested Asset Arguments

Templates may refer to public values on another bound asset:

```python
@asset(
    "bronze/{base_asset.name}_{field}.parquet",
    name="{base_asset.name}_{field}",
)
async def unnested_asset(base_asset: AssquackAsset, field: str):
    ...
```

Binding only the base asset can resolve `base_asset.name` while preserving the
missing `field` placeholder:

```python
partial = unnested_asset.with_arguments(base_asset=customer_asset)
```

The formatter also collects arguments already bound to nested
`AssquackAsset` values. This retains the useful MAD.Prefect composition model
without making Assquack depend on MAD.Prefect or an orchestrator.

Explicit arguments on the outer asset take precedence over collected nested
arguments. Recursive collection tracks visited asset objects so cyclic asset
references cannot recurse indefinitely.

## Placeholder Preservation

Partial formatting uses a mapping whose missing keys return placeholder proxy
objects rather than raising immediately.

The proxy preserves normal named placeholders:

```text
{endpoint}
```

It also preserves dotted attribute and index access:

```text
{base_asset.name}
{items[0]}
```

Formatting repeats when a resolved value introduces another template field.
The formatter stops when the value is fully resolved or when another pass would
repeat the same string. At a strict execution boundary, a stable unresolved
template raises instead of silently becoming an asset identity or table name.

## Resolution Boundaries

Partial resolution is appropriate when:

- the decorator creates the initial `AssquackAsset`;
- `.with_arguments(...)` creates a derived asset;
- `.cache_first()` creates another view of the definition; or
- application code inspects or composes an asset definition.

Strict resolution is required before:

- materializing the asset;
- deriving its final asset ID and DuckDB table;
- querying its published table; or
- checking whether its published table exists.

Python signature binding validates missing required function arguments first.
Template formatting then validates that `export`, `name`, and `table` no longer
contain unresolved fields. These failures happen before source IO or database
materialization starts.

## Current Prototype Location

The behaviour is split between:

```text
src/assquack/_templates.py
src/assquack/_api.py
```

`AssetTemplateFormatter` owns recursive formatting, missing-placeholder
preservation, dotted/index access, and nested bound-argument discovery.
`AssquackAsset` owns the original templates, immutable derivation through
`.with_arguments(...)`, and the switch from partial to strict formatting.

The historical implementation used as design reference is:

```text
.submodules/MAD.Prefect/mad_prefect/data_assets/asset_template_formatter.py
```

It is reference material only and is not a runtime dependency.

## Compatibility With Canonical Docs

The canonical developer API already says bound arguments participate in
resolving templated export aliases, names, and tables. The materialization
lifecycle also requires arguments and identity to resolve before cache lookup
or source IO.

Those documents do not currently prescribe whether formatting must be eager or
allow partial binding. Partial lazy formatting satisfies their existing
requirements without changing database placement, materialization mode, or the
asset decorator surface. Treating it as a guaranteed public contract would
still require explicit approval and a canonical documentation update.

## Known Limits And Open Decisions

- Partial formatting is intended for named fields; positional template fields
  are not part of the current asset-template convention.
- Error types and messages at strict resolution are not yet a stable public
  contract.
- Behaviour for conversion flags and advanced format specifications needs an
  explicit decision if those forms are required.
- Nested argument discovery understands `AssquackAsset` values, not arbitrary
  foreign asset types.

## Non-goals

- Evaluating arbitrary Python expressions inside templates.
- Materializing a nested asset merely because it is supplied as an argument.
- Using templates to configure the DuckDB database path.
- Mutating the original asset when `.with_arguments(...)` is called.
- Introducing a dependency on MAD.Prefect.
