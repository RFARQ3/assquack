from pathlib import Path

import duckdb
import pytest

from assquack._exports.diagnostics import azure_export_error


def test_native_missing_extension_has_installation_guidance(tmp_path: Path):
    # Use an empty cache and disable downloads so this exercises a genuine native
    # error without relying on the workstation's extensions or accessing a network.
    with (
        duckdb.connect(
            config={
                "extension_directory": str(tmp_path),
                "autoload_known_extensions": False,
                "autoinstall_known_extensions": False,
            }
        ) as connection,
        pytest.raises(duckdb.IOException) as caught,
    ):
        connection.execute("LOAD azure")

    message = str(azure_export_error("extension", caught.value))
    assert "loading the DuckDB Azure extension" in message
    assert "IOException" in message
    assert "Install the Azure extension for the running DuckDB version" in message
    assert str(tmp_path) not in message


def test_unknown_exception_class_name_is_not_exposed():
    custom_type = type("credential_canary", (Exception,), {})
    native = custom_type("credential_canary")
    native.add_note("credential_canary")
    message = str(azure_export_error("write", native))

    assert "writing the Azure export (Exception)" in message
    assert "Backend text omitted" in message
    assert "credential_canary" not in message


def test_unprintable_exception_preserves_safe_operation_diagnostic():
    class Unprintable(Exception):
        def __str__(self):
            raise ValueError("credential_canary")

    diagnostic = azure_export_error("authentication", Unprintable())
    assert "registering Azure authentication" in str(diagnostic)
    assert "credential_canary" not in str(diagnostic)
    assert diagnostic.__cause__ is None
    assert diagnostic.__context__ is None
