"""Load explicit credentials independently of DuckDB authentication strategies."""

import re
from dataclasses import dataclass
from os import getenv
from typing import Protocol

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


class AzureSasProvider(Protocol):
    def resolve(self) -> AzureSasCredentials | None:
        """Return a complete credential, None when absent, or a safe error."""
        ...


class EnvironmentSasProvider:
    def resolve(self) -> AzureSasCredentials | None:
        """Read only the explicit SAS pair at use time, never native identity vars."""
        token = (getenv("AZURE_STORAGE_SAS_TOKEN") or "").strip()
        account = (getenv("AZURE_STORAGE_ACCOUNT_NAME") or "").strip()
        if not token and not account:
            return None
        if not token or not account:
            raise ConfigurationError(
                "Azure SAS requires both AZURE_STORAGE_SAS_TOKEN and "
                "AZURE_STORAGE_ACCOUNT_NAME."
            )
        return AzureSasCredentials(account, SecretStr(token.removeprefix("?")))
