import asyncio
import logging
import traceback
from unittest.mock import MagicMock

import duckdb
import pytest

from assquack import FileSystemDestination
from assquack._errors import ExportError
from assquack._storage.tables import TableReference


@pytest.fixture
def destination(monkeypatch):
    environment = {
        "AZURE_STORAGE_ACCOUNT_NAME": "exampleaccount",
        "AZURE_STORAGE_SAS_TOKEN": "sig=synthetic-sensitive",
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


@pytest.mark.parametrize(
    "statement,stage",
    [
        ("LOAD", "loading the DuckDB Azure extension"),
        ("CREATE", "registering Azure authentication"),
        ("COPY", "writing the Azure export"),
        ("DROP", "removing the temporary Azure secret"),
    ],
)
def test_failure_identifies_operation_without_retaining_native_error(
    destination, statement, stage
):
    native = RuntimeError("synthetic-sensitive")
    native.__cause__ = ValueError("cause-sensitive")
    native.add_note("note-sensitive")
    with pytest.raises(ExportError) as caught:
        export(destination, {statement: native})

    public = caught.value
    assert stage in str(public)
    assert "RuntimeError" in str(public)
    assert "Backend text omitted" in str(public)
    assert public.__cause__ is None
    assert public.__context__ is None
    assert "sensitive" not in repr(public)
    assert "sensitive" not in "".join(traceback.format_exception(public))
    assert destination._resolved is None


@pytest.mark.parametrize(
    "native,details",
    [
        (
            duckdb.IOException(
                "AzureBlobStorageFileSystem FileSync of signed-url failed with "
                "AuthorizationPermissionMismatch Reason Phrase: Forbidden; HTTP 403"
            ),
            (
                "IOException",
                "HTTP 403",
                "AuthorizationPermissionMismatch",
                "write permissions",
            ),
        ),
        (
            duckdb.IOException("HTTP status code: 404; <Code>ContainerNotFound</Code>"),
            ("HTTP 404", "ContainerNotFound", "provision it before exporting"),
        ),
        (
            duckdb.IOException("PathNotFound"),
            ("PathNotFound", "required parent directories"),
        ),
        (
            duckdb.IOException(
                "unknown error occurred, this could mean the credentials used were "
                "wrong: Problem with the SSL CA cert (path? access rights?)"
            ),
            ("TLS certificate verification failed", "CA certificates", "proxy trust"),
        ),
        (
            duckdb.IOException("Could not resolve host: private-storage-host"),
            ("DNS resolution failed", "runtime DNS configuration"),
        ),
        (
            duckdb.BinderException("secret column not found in SELECT"),
            ("BinderException", "could not bind the export SQL", "columns"),
        ),
        (
            duckdb.ParserException("syntax error near secret"),
            ("ParserException", "SQL syntax", "extension compatibility"),
        ),
        (
            TimeoutError("private endpoint timed out"),
            ("TimeoutError", "timed out", "network connectivity"),
        ),
        (
            duckdb.IOException("HTTP/1.1 503 Service Unavailable"),
            ("HTTP 503", "service health before retrying"),
        ),
    ],
)
def test_write_diagnostic_preserves_recognised_cause(destination, native, details):
    with pytest.raises(ExportError) as caught:
        export(destination, {"COPY": native})
    message = str(caught.value)
    assert "writing the Azure export" in message
    for detail in details:
        assert detail in message
    assert "registering Azure authentication" not in message
    assert "check authentication and permissions" not in message


@pytest.mark.parametrize(
    "payload",
    [
        "https://user:password@example.invalid/file?sig=synthetic-sensitive#fragment-secret",
        "AccountName=privateaccount;AccountKey=synthetic-sensitive;SharedAccessSignature=sig=token",
        "Authorization: Bearer synthetic-sensitive",
        "AZURE_CLIENT_SECRET='synthetic-sensitive'",
        "sig%3Dsynthetic%252Dsensitive",
        "-----BEGIN PRIVATE KEY-----\nsynthetic-sensitive\n-----END PRIVATE KEY-----",
        "unlabelled arbitrary credential synthetic-sensitive",
        "COPY secret SQL; unknown Azure error code PrivateSensitiveError",
    ],
)
@pytest.mark.parametrize("statement", ["LOAD", "CREATE", "COPY", "DROP"])
def test_backend_payloads_are_not_exposed_in_errors_or_logs(
    destination, payload, statement, caplog
):
    native = duckdb.IOException(payload)
    with pytest.raises(ExportError) as caught:
        export(destination, {statement: native})
    rendered = "".join(traceback.format_exception(caught.value))
    assert payload not in rendered
    assert "synthetic-sensitive" not in rendered
    assert "PrivateSensitiveError" not in rendered
    assert "secret SQL" not in rendered
    assert payload not in caplog.text
    assert caught.value.__context__ is None


def test_cleanup_failure_is_secondary_to_write_failure(destination, caplog):
    caplog.set_level(logging.WARNING)
    write_error = duckdb.IOException("HTTP 403 AuthorizationPermissionMismatch")
    cleanup_error = duckdb.CatalogException("synthetic-sensitive cleanup payload")
    with pytest.raises(ExportError) as caught:
        export(destination, {"COPY": write_error, "DROP": cleanup_error})

    assert "writing the Azure export" in str(caught.value)
    assert "AuthorizationPermissionMismatch" in str(caught.value)
    assert "removing the temporary Azure secret" in caplog.text
    assert "CatalogException" in caplog.text
    assert "discard this connection" in caplog.text
    assert "synthetic-sensitive" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
    assert caught.value.__context__ is None


def test_unknown_exception_names_notes_and_groups_are_not_copied(destination):
    custom_type = type("synthetic-sensitive", (Exception,), {})
    native = ExceptionGroup("group-sensitive", [custom_type("child-sensitive")])
    native.add_note("note-sensitive")
    with pytest.raises(ExportError) as caught:
        export(destination, {"COPY": native})
    assert "(Exception)" in str(caught.value)
    assert "Backend text omitted" in str(caught.value)
    assert "sensitive" not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "interruption", [asyncio.CancelledError(), KeyboardInterrupt()]
)
def test_cleanup_failure_does_not_replace_process_control_exception(
    destination, interruption, caplog
):
    with pytest.raises(type(interruption)) as caught:
        export(
            destination,
            {"COPY": interruption, "DROP": RuntimeError("synthetic-sensitive")},
        )
    assert caught.value is interruption
    assert caught.value.__context__ is None
    assert "synthetic-sensitive" not in caplog.text
