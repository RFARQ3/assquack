"""Project native Azure failures into useful diagnostics without copying payloads."""

import re
from typing import Literal

import duckdb

from assquack._errors import ExportError

ExportStage = Literal["extension", "authentication", "write", "cleanup"]

_STAGES = {
    "extension": "loading the DuckDB Azure extension",
    "authentication": "registering Azure authentication",
    "write": "writing the Azure export",
    "cleanup": "removing the temporary Azure secret",
}
_STAGE_HINTS = {
    "extension": "Install the Azure extension for the running DuckDB version.",
    "authentication": "Check the selected authentication method and its configuration.",
    "write": "Check the export schema, destination and storage access.",
    "cleanup": "The temporary secret could not be removed; discard this connection.",
}
_AZURE_CODES = {
    "AuthenticationFailed": (
        "Azure rejected authentication; check the selected credentials."
    ),
    "AuthorizationFailure": (
        "Azure denied access; check identity permissions and storage network rules."
    ),
    "AuthorizationPermissionMismatch": (
        "Azure denied this operation; check the identity or SAS write permissions."
    ),
    "AuthorizationResourceTypeMismatch": "The SAS does not permit this resource type.",
    "AuthorizationServiceMismatch": "The SAS does not permit this storage service.",
    "ContainerNotFound": (
        "The destination container does not exist; provision it before exporting."
    ),
    "FilesystemNotFound": (
        "The destination filesystem does not exist; provision it before exporting."
    ),
    "PathNotFound": (
        "The destination path does not exist; check required parent directories."
    ),
    "BlobNotFound": "The requested blob does not exist; check the destination path.",
    "InvalidUri": (
        "Azure rejected the destination URI; check its account, container and path."
    ),
    "ServerBusy": "Azure is busy; retry later and review concurrent storage requests.",
    "OperationTimedOut": (
        "Azure timed out; check service health and network connectivity."
    ),
}
_CATEGORIES = (
    (
        duckdb.BinderException,
        "BinderException",
        "DuckDB could not bind the export SQL; "
        "check columns, types and supported COPY options.",
    ),
    (
        duckdb.ParserException,
        "ParserException",
        "DuckDB rejected the SQL syntax; check export SQL and extension compatibility.",
    ),
    (
        duckdb.CatalogException,
        "CatalogException",
        "DuckDB could not resolve a table, function or secret; "
        "check registration and source availability.",
    ),
    (
        duckdb.ConversionException,
        "ConversionException",
        "DuckDB could not convert a value; "
        "check the source column types and export format.",
    ),
    (
        duckdb.NotImplementedException,
        "NotImplementedException",
        "DuckDB does not support this operation; "
        "check the export format and extension version.",
    ),
    (
        duckdb.IOException,
        "IOException",
        "DuckDB could not complete storage I/O; "
        "check destination access and network connectivity.",
    ),
    (
        duckdb.Error,
        "DuckDB Error",
        "DuckDB rejected the operation; "
        "check the export configuration and engine compatibility.",
    ),
    (
        TimeoutError,
        "TimeoutError",
        "The operation timed out; check service health and network connectivity.",
    ),
    (
        ConnectionError,
        "ConnectionError",
        "The connection failed; check DNS, routing and storage network rules.",
    ),
    (
        PermissionError,
        "PermissionError",
        "Access was denied; check filesystem and storage permissions.",
    ),
    (
        FileNotFoundError,
        "FileNotFoundError",
        "A required file or directory was not found; "
        "check runtime and destination paths.",
    ),
    (
        ValueError,
        "ValueError",
        "An input value was rejected; check the export configuration.",
    ),
    (RuntimeError, "RuntimeError", ""),
)
_HTTP_STATUS = re.compile(
    r"\b(?:HTTP(?:/\d(?:\.\d)?)?(?:\s+(?:error|status)(?:\s+code)?)?|"
    r"status(?:\s*code)?)\s*[:=]?\s*([45]\d{2})\b",
    re.IGNORECASE,
)


class AzureExportError(ExportError):
    """A projected diagnostic whose native exception context must be discarded."""


def azure_export_error(stage: ExportStage, error: Exception) -> AzureExportError:
    """Retain only recognised facts; backend text may contain secrets anywhere."""
    category, hint = "Exception", ""
    for exception_type, label, explanation in _CATEGORIES:
        if isinstance(error, exception_type):
            category, hint = label, explanation
            break

    # Never interpolate a backend string, class name, note or URL into the result.
    # Even apparently harmless SQL diagnostics can embed credentials as values.
    try:
        message = str(error)
    except Exception:
        message = ""
    facts = [category]
    status = _HTTP_STATUS.search(message)
    if status:
        facts.append(f"HTTP {int(status[1])}")
        if status[1] == "403":
            hint = _AZURE_CODES["AuthorizationFailure"]
        elif status[1] == "401":
            hint = _AZURE_CODES["AuthenticationFailed"]
        elif status[1] == "404":
            hint = (
                "Azure could not find the resource; "
                "check the account, container and path."
            )
        elif status[1] == "429" or status[1].startswith("5"):
            hint = (
                "The service could not complete the request; "
                "check service health before retrying."
            )

    for code, explanation in _AZURE_CODES.items():
        if re.search(r"\b" + code + r"\b", message):
            facts.append(code)
            hint = explanation
            break

    # Native wrappers sometimes suggest bad credentials even for TLS failures.
    # Prefer the concrete transport signature over that generic suggestion.
    if re.search(
        r"SSL CA cert|certificate verify failed|certificate verification failed|"
        r"unable to get local issuer certificate|SSL peer certificate",
        message,
        re.IGNORECASE,
    ):
        hint = (
            "TLS certificate verification failed; "
            "check the runtime CA certificates and proxy trust configuration."
        )
    elif re.search(
        r"could not resolve host|name or service not known|name resolution",
        message,
        re.IGNORECASE,
    ):
        hint = (
            "DNS resolution failed; "
            "check the storage endpoint and runtime DNS configuration."
        )

    if stage in ("extension", "cleanup") and hint:
        hint = f"{hint} {_STAGE_HINTS[stage]}"
    return AzureExportError(
        f"Azure export failed while {_STAGES[stage]} ({'; '.join(facts)}). "
        f"{hint or _STAGE_HINTS[stage]} "
        "Backend text omitted to protect credentials."
    )
