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


class CredentialProvider(Protocol):
    async def load(self) -> None: ...

    def get(self, name: str) -> str | None: ...


class EnvironmentCredentialProvider:
    async def load(self) -> None:
        pass

    def get(self, name: str) -> str | None:
        return getenv(name)


def load_provider(provider: CredentialProvider) -> None:
    """Join async loading to sync export calls, including calls inside an event loop."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(provider.load())
        else:
            # A synchronous export cannot nest run_until_complete on its caller's loop.
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(lambda: asyncio.run(provider.load())).result()
    except Exception:
        raise ConfigurationError("Could not load export credentials.") from None


def load_credentials(provider: CredentialProvider) -> AzureSasCredentials | None:
    load_provider(provider)
    try:
        token = (provider.get("AZURE_STORAGE_SAS_TOKEN") or "").strip()
        account = (provider.get("AZURE_STORAGE_ACCOUNT_NAME") or "").strip()
    except Exception:
        raise ConfigurationError("Could not load export credentials.") from None
    if not token and not account:
        return None
    if not token or not account:
        raise ConfigurationError(
            "Azure SAS requires both AZURE_STORAGE_SAS_TOKEN and "
            "AZURE_STORAGE_ACCOUNT_NAME."
        )
    return AzureSasCredentials(account, SecretStr(token.removeprefix("?")))


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


def chain_environment(provider: CredentialProvider) -> dict[str, SecretStr]:
    """Capture only documented chain inputs from an already loaded provider."""
    try:
        return {
            name: SecretStr(value)
            for name in CHAIN_ENVIRONMENT_NAMES
            if (value := provider.get(name))
        }
    except Exception:
        raise ConfigurationError("Could not load credential-chain inputs.") from None
