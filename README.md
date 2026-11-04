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
- [Developer API](docs/developer-api.md)
- [Developer examples](docs/developer-examples.md)

## Development

Install the project and development tooling with Poetry:

```bash
poetry install
```

Run the bootstrap checks with:

```bash
poetry run pytest
poetry run pyright
poetry run ruff check .
poetry run ruff format --check .
```

Keep local scratch artifacts under the ignored `.tmp/` directory, with a
subdirectory per task or benchmark. For example, use
`poetry run pytest --basetemp .tmp/my-check/pytest` for a local test run.
Pytest clears its selected base directory, so keep other artifacts outside it.
Historical benchmark results retain the original paths recorded at run time.
