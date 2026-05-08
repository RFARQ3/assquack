# Assquack

Working repository for the DuckDB-native data asset implementation currently
called `assquack`.

The project uses `.submodules/MAD.Prefect` as reference material for prior data
asset ergonomics, but Assquack itself is a standalone library. The core package
must not depend on Prefect or any orchestrator/deployment platform; MAD.Prefect
is historical reference material only.

Current documentation entry points:

- [Documentation index](docs/README.md)
- [Overview](docs/overview.md)
- [Developer examples](docs/developer-examples.md)
