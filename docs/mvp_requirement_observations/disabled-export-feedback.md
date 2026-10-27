# Disabled Export Feedback

Status: Historical observation; superseded 27 October 2026

The destination refactor intentionally makes `exports=[]` skip automatic exports
without an error. See [current configuration](../configuration.md#exports). The
prototype behaviour below is retained as historical context.

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

Assquack completes table materialization before reporting a disabled declared
export. The current table is published and the run is marked successful, then
the export phase raises when:

- the resolved asset declares an export target; and
- `exports.enabled` is `false`.

The error includes the resolved export target and both supported remedies:

```text
Set ASSQUACK_EXPORTS__ENABLED=true, or pass
AssquackConfig(exports=ExportsConfig(enabled=True)).
```

This keeps the successful DuckDB table available while clearly reporting that
the requested compatibility artifact was not written. Assets without an export
target continue to materialize normally while exports are disabled.

## Current Prototype Location

```text
src/assquack/_materialization/pipeline.py
tests/test_exports.py
```

## Compatibility With Canonical Docs

The canonical docs define exports as optional and disabled by default. They do
not require Assquack to silently discard an explicitly declared export target.
Reporting the failure after the table commit preserves DuckDB as the source of
truth while making the missing compatibility artifact visible to the caller.
