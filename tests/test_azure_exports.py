import asyncio
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import duckdb
import pytest
from pydantic import ValidationError

from assquack import AssquackConfig, ExportsConfig, asset
from assquack._errors import ExportError
from assquack._exports.azure import azure_export_auth
from assquack._exports.models import ExportTarget
from assquack._exports.parquet import ParquetExportWriter
from assquack._storage.database import open_database
from assquack._storage.tables import TableReference

URI = "abfss://exampleaccount.dfs.core.windows.net/lake/orders.parquet"


@pytest.fixture(autouse=True)
def isolated_sas_environment(monkeypatch):
    # Replace the read boundary, without retrieving any real credential values.
    environment: dict[str, str] = {}
    monkeypatch.setattr("assquack._exports.azure_credentials.getenv", environment.get)
    return environment


def test_azure_config_defaults_environment_and_override(monkeypatch) -> None:
    for name in ("AZURE_CHAIN", "AZURE_ACCOUNT_NAME"):
        monkeypatch.delenv(f"ASSQUACK_EXPORTS__{name}", raising=False)
    assert ExportsConfig().azure_chain is None
    assert ExportsConfig().azure_account_name is None
    monkeypatch.setenv("ASSQUACK_EXPORTS__AZURE_CHAIN", "workload_identity")
    monkeypatch.setenv("ASSQUACK_EXPORTS__AZURE_ACCOUNT_NAME", "exampleaccount")
    assert AssquackConfig().exports.azure_chain == "workload_identity"
    assert AssquackConfig().exports.azure_account_name == "exampleaccount"
    assert ExportsConfig(azure_chain=None, azure_account_name=None).azure_chain is None
    with pytest.raises(ValidationError):
        ExportsConfig.model_validate({"azure_chain": "cli;env"})
    with pytest.raises(ValidationError):
        ExportsConfig(azure_account_name="invalid'account")


@pytest.mark.parametrize("chain", [None, "workload_identity"])
def test_secret_is_temporary_scoped_and_removed(chain) -> None:
    connection = MagicMock(spec=duckdb.DuckDBPyConnection)
    config = ExportsConfig(azure_chain=chain, azure_account_name="exampleaccount")
    uri = URI.replace("orders", "O'Brien")
    with azure_export_auth(connection, uri, config):
        connection.execute("COPY marker")
    calls = connection.execute.call_args_list
    assert calls[0].args == ("LOAD azure",)
    sql, parameters = calls[1].args
    assert sql.startswith("CREATE TEMPORARY SECRET")
    assert "PROVIDER credential_chain" in sql
    assert parameters["scope"] == uri.rsplit("/", 1)[0] + "/"
    assert parameters["account_name"] == "exampleaccount"
    assert ("CHAIN $chain" in sql) == (chain is not None)
    assert parameters.get("chain") == chain
    assert calls[-1].args[0].startswith("DROP TEMPORARY SECRET")
    assert calls[-1].args[0].split('"')[1] == sql.split('"')[1]
    # Parsing is real DuckDB; no extension loading or identity discovery occurs.
    with duckdb.connect(config={"autoload_known_extensions": False}) as parser:
        assert len(parser.extract_statements(sql)) == 1
        assert len(parser.extract_statements(calls[-1].args[0])) == 1


def test_cleanup_does_not_mask_copy_failure() -> None:
    connection = MagicMock(spec=duckdb.DuckDBPyConnection)
    connection.execute.side_effect = [None, None, RuntimeError("cleanup failed")]
    with (
        pytest.raises(ValueError, match="copy failed"),
        azure_export_auth(connection, URI, ExportsConfig()),
    ):
        raise ValueError("copy failed")


@pytest.mark.parametrize("scheme", ["az", "azure", "abfss"])
def test_short_azure_uri_requires_account_and_binds_destination(scheme) -> None:
    connection = MagicMock(spec=duckdb.DuckDBPyConnection)
    uri = f"{scheme}://lake/O'Brien.parquet"
    target = ExportTarget(uri, uri, uri)
    source = TableReference("main", "orders")
    with pytest.raises(Exception, match="azure_account_name"):
        ParquetExportWriter(ExportsConfig()).write(connection, source, target)
    connection.execute.assert_not_called()
    ParquetExportWriter(ExportsConfig(azure_account_name="exampleaccount")).write(
        connection, source, target
    )
    sql, parameters = connection.execute.call_args_list[2].args
    assert "TO $uri" in sql
    assert parameters == {"uri": uri}


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
        exports=ExportsConfig(
            enabled=True,
            base_uri=URI.rsplit("/", 1)[0],
            azure_auth=auth,
            azure_chain="cli" if auth == "credential_chain" else None,
        ),
    )

    @asset(None if explicit else "orders.parquet", table="main.orders")
    def orders():
        return [{"id": 1}]

    def run():
        result = asyncio.run(orders(assquack_config=config))
        if explicit:
            assert result.export_parquet(URI) == URI

    if failure is None:
        run()
    else:
        error_type = RuntimeError if auth == "credential_chain" else ExportError
        message = (
            "synthetic export failure"
            if auth == "credential_chain"
            else "Azure SAS export failed"
        )
        with pytest.raises(error_type, match=message):
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

    @asset("orders.parquet", table="main.orders")
    def orders():
        if fail:
            raise ValueError("extraction failed")
        return [{"id": 1}]

    config = AssquackConfig(
        home=tmp_path, exports=ExportsConfig(enabled=True, azure_chain="cli")
    )
    result = asyncio.run(orders(assquack_config=config))
    output = Path(config.exports.base_uri or "") / "orders.parquet"
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
