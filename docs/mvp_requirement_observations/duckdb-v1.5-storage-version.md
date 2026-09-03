# DuckDB 1.5 Storage Version

Status: Working MVP requirement observation

This document records prototype behaviour and does not change the contracts in
the canonical documentation.

## Observed Problem

Installing DuckDB 1.5 does not make new database files use the 1.5 storage
format automatically. DuckDB defaults to an older storage format for forward
compatibility with older readers.

Assquack stores raw evidence in `VARIANT` columns. DuckDB cannot persist those
columns in storage formats earlier than version 1.5, so materialization fails
when the database retains the default older format.

## MVP Behaviour

Assquack opens its database with an explicit storage compatibility version:

```python
config={"storage_compatibility_version": "v1.5.0"}
```

The version is an internal Assquack requirement rather than normal runtime
configuration. Allowing a lower value would make the required `VARIANT` storage
contract invalid.

## Existing Databases

An existing database created with an older storage format may need to be
recreated for local development or migrated into a new v1.5-format database.
Choosing v1.5 means DuckDB versions older than 1.5 must not be expected to read
the resulting database file.

## Current Prototype Location

```text
src/assquack/_storage/database.py
```

## Compatibility With Canonical Docs

The package already requires DuckDB 1.5 or newer, and the canonical storage and
schema contracts require durable `VARIANT` columns. Explicitly selecting the
v1.5 storage format makes the database file satisfy those existing contracts;
it does not introduce a new public configuration option.
