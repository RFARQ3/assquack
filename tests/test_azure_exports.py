import asyncio
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import duckdb
import pytest
from pydantic import SecretStr

from assquack import AssquackConfig, FileSystemDestination, asset
from assquack._errors import ExportError
from assquack._exports.authentication.azure import CredentialChainAuth
from assquack._exports.preparation.azure import AzurePreparationStrategy
from assquack._storage.database import open_database

URI = "abfss://exampleaccount.dfs.core.windows.net/lake/orders.parquet"


@pytest.fixture(autouse=True)
def isolated_sas_environment(monkeypatch):
    # Replace the read boundary, without retrieving any real credential values.
    environment = {
        "AZURE_TENANT_ID": "tenant",
        "AZURE_CLIENT_ID": "client",
        "AZURE_CLIENT_SECRET": "synthetic-secret",
    }
    monkeypatch.setattr(
        "assquack._exports.authentication.credentials.getenv", environment.get
    )
    return environment


def test_secret_is_temporary_scoped_and_removed() -> None:
    connection = MagicMock(spec=duckdb.DuckDBPyConnection)
    uri = URI.replace("orders", "O'Brien")
    with AzurePreparationStrategy().prepare(
        connection,
        uri,
        CredentialChainAuth(
            {
                name: SecretStr(value)
                for name, value in {
                    "AZURE_TENANT_ID": "tenant",
                    "AZURE_CLIENT_ID": "client",
                    "AZURE_CLIENT_SECRET": "synthetic-secret",
                }.items()
            }
        ),
    ):
        connection.execute("COPY marker")
    calls = connection.execute.call_args_list
    assert calls[0].args == ("LOAD azure",)
    sql, parameters = calls[1].args
    assert sql.startswith("CREATE TEMPORARY SECRET")
    assert "PROVIDER credential_chain" in sql
    assert parameters == {"chain": "env", "scope": uri.rsplit("/", 1)[0] + "/"}
    assert calls[-1].args[0].startswith("DROP TEMPORARY SECRET")
    assert calls[-1].args[0].split('"')[1] == sql.split('"')[1]
    with duckdb.connect(config={"autoload_known_extensions": False}) as parser:
        assert len(parser.extract_statements(sql)) == 1
        assert len(parser.extract_statements(calls[-1].args[0])) == 1


@pytest.mark.parametrize("scheme", ["az", "azure", "abfss"])
def test_short_azure_uri_requires_sas_account(scheme) -> None:
    connection = MagicMock(spec=duckdb.DuckDBPyConnection)
    uri = f"{scheme}://lake/orders.parquet"
    with pytest.raises(ExportError, match="account supplied by SAS"):
        FileSystemDestination(base_uri=uri).resolve(uri, asset_name="orders")
    connection.execute.assert_not_called()


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("auth", ["credential_chain", "sas"])
@pytest.mark.parametrize("failure", [None, "CREATE TEMPORARY", "COPY"])
def test_actual_export_connection_lifecycle_and_manifest(
    tmp_path: Path,
    monkeypatch,
    explicit: bool,
    failure: str | None,
    auth,
    isolated_sas_environment,
) -> None:
    connections: list[MagicMock] = []

    def intercepted_open(config):
        real = open_database(config)
        proxy = MagicMock(wraps=real)

        def execute(sql, parameters=None):
            statement = sql.strip()
            if statement.startswith(
                ("LOAD azure", "CREATE TEMPORARY", "DROP TEMPORARY", "COPY")
            ):
                if failure is not None and statement.startswith(failure):
                    raise RuntimeError("synthetic export failure")
                return proxy
            return real.execute(sql, parameters)

        proxy.execute.side_effect = execute
        connections.append(proxy)
        return cast(duckdb.DuckDBPyConnection, proxy)

    monkeypatch.setattr(
        "assquack._materialization.pipeline.open_database", intercepted_open
    )
    monkeypatch.setattr("assquack._result.open_database", intercepted_open)
    isolated_sas_environment.update(
        AZURE_STORAGE_ACCOUNT_NAME="exampleaccount",
        AZURE_STORAGE_SAS_TOKEN="sig=synthetic%2Bsignature%3D",
    )
    config = AssquackConfig(
        home=tmp_path,
        exports=FileSystemDestination(
            base_uri=URI.rsplit("/", 1)[0],
            auth=auth,
        ),
    )

    @asset(table="main.orders", export=None if explicit else "orders.parquet")
    def orders():
        return [{"id": 1}]

    def run():
        result = asyncio.run(orders(assquack_config=config))
        if explicit:
            assert result.export_parquet(URI) == URI

    if failure is None:
        run()
    else:
        with pytest.raises(ExportError, match="Azure export failed"):
            run()
    export_connection = connections[-1]
    statements = [
        call.args[0].strip() for call in export_connection.execute.call_args_list
    ]
    assert sum(sql.startswith("CREATE TEMPORARY") for sql in statements) == 1
    assert any(sql.startswith("COPY") for sql in statements) == (
        failure != "CREATE TEMPORARY"
    )
    assert any(sql.startswith("DROP TEMPORARY") for sql in statements) == (
        failure != "CREATE TEMPORARY"
    )
    for connection in connections:
        connection.close.assert_called_once()
    with duckdb.connect(str(config.resolved_database_path)) as check:
        assert check.execute("SELECT id FROM main.orders").fetchall() == [(1,)]
        assert check.execute("SELECT status FROM assquack_meta.runs").fetchall() == [
            ("success",)
        ]
        manifests = check.execute(
            "SELECT resolved_uri FROM assquack_meta.exports"
        ).fetchall()
        assert manifests == ([(URI,)] if failure is None else [])


def test_local_exports_do_not_register_azure_and_failed_refresh_preserves_file(
    tmp_path: Path,
    isolated_sas_environment,
) -> None:
    isolated_sas_environment["AZURE_STORAGE_SAS_TOKEN"] = "incomplete-and-invalid"
    fail = False

    @asset(table="main.orders", export="orders.parquet")
    def orders():
        if fail:
            raise ValueError("extraction failed")
        return [{"id": 1}]

    config = AssquackConfig(
        home=tmp_path, exports=FileSystemDestination(base_uri=str(tmp_path / "exports"))
    )
    result = asyncio.run(orders(assquack_config=config))
    output = (tmp_path / "exports") / "orders.parquet"
    original = output.read_bytes()
    fail = True
    with pytest.raises(ValueError, match="extraction failed"):
        asyncio.run(orders(assquack_config=config))
    assert output.read_bytes() == original
    assert result.export_parquet(str(tmp_path / "O'Brien.parquet"))


def test_database_setup_failure_closes_connection(tmp_path: Path, monkeypatch) -> None:
    connection = MagicMock(spec=duckdb.DuckDBPyConnection)
    connection.execute.side_effect = RuntimeError("setup failed")
    monkeypatch.setattr(
        "assquack._storage.database.duckdb.connect", lambda *a, **kw: connection
    )
    with pytest.raises(RuntimeError, match="setup failed"):
        open_database(AssquackConfig(home=tmp_path))
    connection.close.assert_called_once()
