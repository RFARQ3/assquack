import asyncio
import logging
import traceback
from unittest.mock import MagicMock

import duckdb
import pytest

from assquack import FileSystemDestination
from assquack._exports.authentication.azure import CredentialChainAuth
from assquack._exports.preparation.azure import AzurePreparationStrategy
from assquack._storage.tables import TableReference


@pytest.fixture
def destination(monkeypatch):
    environment = {
        "AZURE_STORAGE_ACCOUNT_NAME": "exampleaccount",
        "AZURE_STORAGE_SAS_TOKEN": "sig=synthetic-token",
    }
    monkeypatch.setattr(
        "assquack._exports.authentication.credentials.getenv", environment.get
    )
    return FileSystemDestination(base_uri="abfss://lake", auth="sas")


def export(destination, failures):
    target = destination.resolve("orders.parquet", asset_name="orders")[0]
    connection = MagicMock(spec=duckdb.DuckDBPyConnection)

    def execute(sql, parameters=None):
        for statement, error in failures.items():
            if sql.startswith(statement):
                raise error

    connection.execute.side_effect = execute
    destination.export(connection, TableReference("main", "orders"), target)


@pytest.mark.parametrize("statement", ["LOAD", "CREATE", "COPY", "DROP"])
@pytest.mark.parametrize(
    "exception_type", [duckdb.IOException, duckdb.BinderException, RuntimeError]
)
def test_native_failure_propagates_unchanged(destination, statement, exception_type):
    native = exception_type("Unrecognised native detail with sig=synthetic-token")
    cause = ValueError("native cause")
    context = OSError("native context")
    native.__cause__ = cause
    native.__context__ = context
    native.add_note("Native diagnostic note")

    with pytest.raises(exception_type) as caught:
        export(destination, {statement: native})

    assert caught.value is native
    assert str(caught.value) == "Unrecognised native detail with sig=synthetic-token"
    assert caught.value.__cause__ is cause
    assert caught.value.__context__ is context
    assert caught.value.__notes__ == ["Native diagnostic note"]
    rendered = "".join(traceback.format_exception(caught.value))
    assert "native cause" in rendered
    assert "sig=synthetic-token" in rendered
    assert "Native diagnostic note" in rendered
    frames = traceback.extract_tb(caught.value.__traceback__)
    assert frames[-1].name == "execute"
    assert destination._resolved is None


def test_cleanup_failure_is_logged_without_replacing_write_failure(destination, caplog):
    caplog.set_level(logging.WARNING)
    write_error = duckdb.IOException("Native write failure")
    write_cause = ValueError("Original cause")
    write_error.__cause__ = write_cause
    write_error.add_note("Original note")
    cleanup_error = duckdb.CatalogException("Native cleanup failure")

    with pytest.raises(duckdb.IOException) as caught:
        export(destination, {"COPY": write_error, "DROP": cleanup_error})

    assert caught.value is write_error
    assert caught.value.__cause__ is write_cause
    assert caught.value.__context__ is None
    assert caught.value.__notes__ == ["Original note"]
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.getMessage() == "Could not remove temporary Azure export secret."
    assert record.exc_info is not None
    assert record.exc_info[1] is cleanup_error
    assert "Native cleanup failure" in caplog.text
    assert "CONNECTION_STRING" not in record.getMessage()
    assert "sig=synthetic-token" not in record.getMessage()


def test_exception_groups_and_custom_types_propagate_unchanged(destination):
    class CustomBackendFailure(Exception):
        pass

    child = CustomBackendFailure("Unrecognised backend detail")
    native = ExceptionGroup("Multiple native failures", [child])
    native.add_note("Group detail")
    with pytest.raises(ExceptionGroup) as caught:
        export(destination, {"COPY": native})
    assert caught.value is native
    assert caught.value.exceptions[0] is child
    assert caught.value.__notes__ == ["Group detail"]


@pytest.mark.parametrize(
    "interruption", [asyncio.CancelledError(), KeyboardInterrupt()]
)
def test_cleanup_failure_does_not_replace_process_control_exception(
    destination, interruption, caplog
):
    cleanup_error = RuntimeError("Cleanup detail")
    with pytest.raises(type(interruption)) as caught:
        export(destination, {"COPY": interruption, "DROP": cleanup_error})
    assert caught.value is interruption
    assert caught.value.__context__ is None
    assert "Cleanup detail" in caplog.text


def test_native_missing_extension_error_passes_through_preparation(tmp_path):
    # The empty cache and disabled automatic installation keep this fully offline.
    with (
        duckdb.connect(
            config={
                "extension_directory": str(tmp_path),
                "autoload_known_extensions": False,
                "autoinstall_known_extensions": False,
            }
        ) as connection,
        pytest.raises(duckdb.IOException) as caught,
        AzurePreparationStrategy().prepare(
            connection,
            "abfss://exampleaccount.dfs.core.windows.net/lake/orders.parquet",
            CredentialChainAuth({}),
        ),
    ):
        pytest.fail("The missing extension must prevent export")
    assert "azure" in str(caught.value)
    assert "INSTALL azure" in str(caught.value)
    assert "Backend text omitted" not in str(caught.value)
