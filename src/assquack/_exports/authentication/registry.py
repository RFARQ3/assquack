import logging
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import SecretStr

from assquack._errors import ConfigurationError
from assquack._exports.authentication.azure import CredentialChainAuth, SasAuth
from assquack._exports.authentication.base import Authenticator
from assquack._exports.authentication.credentials import (
    CHAIN_REQUIREMENTS,
    AzureSasCredentials,
    has_required_inputs,
    load_inputs,
)
from assquack._exports.authentication.local import LocalAuthenticator

AuthFactory = Callable[[dict[str, SecretStr]], Authenticator]
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthenticationRegistration:
    factory: AuthFactory
    acceptable_input_combinations: tuple[tuple[str, ...], ...]
    optional_inputs: tuple[str, ...]

    @property
    def input_names(self) -> tuple[str, ...]:
        names = list(self.optional_inputs)
        for combination in self.acceptable_input_combinations:
            names.extend(combination)
        return tuple(dict.fromkeys(names))


class AuthenticationRegistry:
    def __init__(self) -> None:
        self._registrations: dict[tuple[str, str], AuthenticationRegistration] = {}

    def register(
        self,
        kind: str,
        method: str,
        factory: AuthFactory,
        acceptable_input_combinations: tuple[tuple[str, ...], ...] = ((),),
        optional_inputs: tuple[str, ...] = (),
    ) -> None:
        key = (kind, method)
        if key in self._registrations:
            raise ValueError(f"Authentication already registered for {key!r}.")
        self._registrations[key] = AuthenticationRegistration(
            factory, acceptable_input_combinations, optional_inputs
        )

    def select(self, kind: str, method: str) -> Authenticator:
        """Capture configured inputs and select the first complete registration."""
        candidates: list[tuple[str, AuthenticationRegistration]] = []
        for key, registration in self._registrations.items():
            registered_kind, registered_method = key
            if registered_kind == kind and (
                method == "auto" or registered_method == method
            ):
                candidates.append((registered_method, registration))
        if not candidates:
            raise ConfigurationError(
                f"Unsupported authentication {method!r} for destination kind {kind!r}."
            )

        names = tuple(
            dict.fromkeys(
                name for _, candidate in candidates for name in candidate.input_names
            )
        )
        available_inputs = load_inputs(names)
        if kind == "azure":
            # Log names only, including before missing configuration raises. Passing
            # input objects to a logger would retain credentials in queued records.
            _LOGGER.info(
                "Azure authentication inputs: present=[%s]; absent=[%s].",
                ", ".join(name for name in names if name in available_inputs),
                ", ".join(name for name in names if name not in available_inputs),
            )
        for registered_method, candidate in candidates:
            if has_required_inputs(
                available_inputs, candidate.acceptable_input_combinations
            ):
                # Retain only the selected strategy's inputs for the export.
                inputs = {
                    name: available_inputs[name]
                    for name in candidate.input_names
                    if name in available_inputs
                }
                authentication = candidate.factory(inputs)
                if kind == "azure":
                    _LOGGER.info(
                        "Azure authentication selected: method=%s; "
                        "configured chain=%s.",
                        registered_method,
                        authentication.chain
                        if isinstance(authentication, CredentialChainAuth)
                        else "not applicable",
                    )
                return authentication

        alternatives = " or ".join(
            " + ".join(combination)
            for _, candidate in candidates
            for combination in candidate.acceptable_input_combinations
        )
        raise ConfigurationError(
            f"Authentication {method!r} for {kind!r} requires {alternatives}."
        )


def _sas(inputs: dict[str, SecretStr]) -> Authenticator:
    account = inputs["AZURE_STORAGE_ACCOUNT_NAME"].get_secret_value().strip()
    token = inputs["AZURE_STORAGE_SAS_TOKEN"].get_secret_value().strip()
    credentials = AzureSasCredentials(account, SecretStr(token.removeprefix("?")))
    return SasAuth(credentials)


def default_authenticators() -> AuthenticationRegistry:
    registry = AuthenticationRegistry()
    registry.register("local", "none", lambda _: LocalAuthenticator())
    # Registration order is automatic selection priority, never runtime failover.
    registry.register(
        "azure",
        "credential_chain",
        CredentialChainAuth,
        acceptable_input_combinations=tuple(
            required for _, required in CHAIN_REQUIREMENTS
        ),
        optional_inputs=("AZURE_AUTHORITY_HOST",),
    )
    registry.register(
        "azure",
        "sas",
        _sas,
        acceptable_input_combinations=(
            ("AZURE_STORAGE_SAS_TOKEN", "AZURE_STORAGE_ACCOUNT_NAME"),
        ),
    )
    return registry
