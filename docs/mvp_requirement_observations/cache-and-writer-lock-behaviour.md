# Cache And Writer Lock Behaviour

Status: Working MVP requirement observation

This document records prototype behaviour and does not change the contracts in
the canonical documentation.

## Cache Behaviour

The prototype accepts an optional `timedelta` through `cache_first(expiration=)`.
With no expiration, the latest successful run does not age out. A declared
export target requires a matching export-manifest row for the currently resolved
target. File and remote-object readability checks are not implemented.

## Writer Lock Behaviour

The prototype creates a sidecar lock file beside the configured DuckDB database.
Async materializations wait without blocking their event loop. Separate Python
processes or Kubernetes pods using the same database path coordinate through the
same operating-system file lock when the shared filesystem supports file
locking.

## Current Prototype Locations

```text
src/assquack/_api.py
src/assquack/_cache.py
src/assquack/_materialization/pipeline.py
src/assquack/_storage/locking.py
tests/test_cache.py
tests/test_writer_lock.py
```

## Compatibility With Canonical Docs

The canonical docs require cache freshness checks and a writer lock, but they do
not prescribe these exact defaults, public arguments, or locking mechanism.
These choices remain prototype observations pending review.
