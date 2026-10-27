from collections.abc import Callable

from assquack._errors import ConfigurationError
from assquack._exports.authentication.azure import CredentialChainAuth, SasAuth
from assquack._exports.authentication.base import Authenticator
from assquack._exports.authentication.credentials import (
    CredentialProvider,
    chain_environment,
    load_credentials,
    load_provider,
)
from assquack._exports.authentication.local import LocalAuthenticator

AuthFactory = Callable[[CredentialProvider], Authenticator]


class AuthenticationRegistry:
    def __init__(self) -> None:
        self._factories: dict[tuple[str, str], AuthFactory] = {}

    def register(self, kind: str, method: str, factory: AuthFactory) -> None:
        key = (kind, method)
        if key in self._factories:
            raise ValueError(f"Authentication already registered for {key!r}.")
        self._factories[key] = factory

    def select(
        self, kind: str, method: str, provider: CredentialProvider
    ) -> Authenticator:
        try:
            factory = self._factories[kind, method]
        except KeyError:
            raise ConfigurationError(
                f"Unsupported authentication {method!r} for destination kind {kind!r}."
            ) from None
        return factory(provider)


def _sas(provider: CredentialProvider) -> Authenticator:
    credentials = load_credentials(provider)
    if credentials is None:
        raise ConfigurationError(
            "SAS authentication requires AZURE_STORAGE_SAS_TOKEN and "
            "AZURE_STORAGE_ACCOUNT_NAME."
        )
    return SasAuth(credentials)


def _azure_auto(provider: CredentialProvider) -> Authenticator:
    credentials = load_credentials(provider)
    if credentials is None:
        return CredentialChainAuth(chain_environment(provider))
    return SasAuth(credentials)


def _credential_chain(provider: CredentialProvider) -> Authenticator:
    load_provider(provider)
    return CredentialChainAuth(chain_environment(provider))


def default_authenticators() -> AuthenticationRegistry:
    registry = AuthenticationRegistry()
    registry.register("local", "auto", lambda _: LocalAuthenticator())
    registry.register("local", "none", lambda _: LocalAuthenticator())
    registry.register("azure", "auto", _azure_auto)
    registry.register("azure", "sas", _sas)
    registry.register("azure", "credential_chain", _credential_chain)
    return registry
