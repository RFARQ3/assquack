"""Load explicit credentials independently of DuckDB authentication strategies."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from os import getenv

from pydantic import SecretStr

from assquack._errors import ConfigurationError


@dataclass(frozen=True)
class AzureSasCredentials:
    account_name: str
    token: SecretStr

    def __post_init__(self) -> None:
        if re.fullmatch(r"[a-z0-9]{3,24}", self.account_name) is None:
            raise ConfigurationError("Azure SAS account name is invalid.")
        token = self.token.get_secret_value()
        # Keep the encoded query intact. A semicolon could introduce a new
        # connection-string field; URLs and raw control characters are not tokens.
        if (
            not token
            or any(character in token for character in ";?#")
            or any(not 33 <= ord(character) <= 126 for character in token)
            or "://" in token
            or not any(
                part.startswith("sig=") and len(part) > 4 for part in token.split("&")
            )
        ):
            raise ConfigurationError(
                "Azure SAS token must be an encoded SAS query token."
            )


CHAIN_ENVIRONMENT_NAMES = (
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    "AZURE_CLIENT_CERTIFICATE_PATH",
    "AZURE_AUTHORITY_HOST",
    "AZURE_FEDERATED_TOKEN_FILE",
)

# Each entry is one complete authentication option, not a requirement for all options.
CHAIN_REQUIREMENTS = (
    ("env", ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET")),
    ("env", ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_CERTIFICATE_PATH")),
    (
        "workload_identity",
        ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_FEDERATED_TOKEN_FILE"),
    ),
)


def has_required_inputs(
    available_inputs: Mapping[str, SecretStr],
    acceptable_input_combinations: tuple[tuple[str, ...], ...],
) -> bool:
    """Accept any complete combination of named inputs."""
    return any(
        all(name in available_inputs for name in combination)
        for combination in acceptable_input_combinations
    )


def load_inputs(names: tuple[str, ...]) -> dict[str, SecretStr]:
    """Capture each requested nonblank environment value once per resolution."""
    if not names:
        return {}
    try:
        return {
            name: SecretStr(value)
            for name in names
            if (value := getenv(name)) and value.strip()
        }
    except Exception:
        raise ConfigurationError("Could not load export credentials.") from None
