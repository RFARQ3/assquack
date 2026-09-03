# Disabled Export Feedback

Status: Working MVP requirement observation

This document records prototype behaviour and does not change the contracts in
the canonical documentation.

## Observed Problem

Compatibility exports are disabled by default, while an asset may explicitly
declare an export target through the positional decorator argument or
`export=`.

Silently ignoring that target makes a successful table materialization appear
to include a successful export. The caller receives no indication that the
declared artifact was not written.

## MVP Behaviour

Materialization fails before opening DuckDB or running the asset function when:

- the resolved asset declares an export target; and
- `exports.enabled` is `false`.

The error includes the resolved export target and both supported remedies:

```text
Set ASSQUACK_EXPORTS__ENABLED=true, or pass
AssquackConfig(exports=ExportsConfig(enabled=True)).
```

Assets without an export target continue to materialize normally while exports
are disabled.

## Current Prototype Location

```text
src/assquack/_materialization/pipeline.py
tests/test_exports.py
```

## Compatibility With Canonical Docs

The canonical docs define exports as optional and disabled by default. They do
not require Assquack to silently discard an explicitly declared export target.
Failing early preserves the documented default while making its consequence and
remedies visible to the caller.
