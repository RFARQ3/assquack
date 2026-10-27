from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import cast

from pydantic import SecretStr

from assquack._errors import ConfigurationError
from assquack._exports.authentication.base import Authenticator
from assquack._exports.preparation.registry import PreparationStrategy


def destination_fingerprint(
    configuration: Mapping[str, object],
    preparation: PreparationStrategy,
    authentication: Authenticator,
) -> str:
    """Identify the destination configuration, independently of output file paths."""
    return hash_configuration(
        {
            "destination": configuration,
            "preparation": {
                "type": type(preparation).__module__
                + "."
                + type(preparation).__qualname__,
                "inputs": preparation.fingerprint_inputs,
            },
            "authentication": {
                "type": type(authentication).__module__
                + "."
                + type(authentication).__qualname__,
                "inputs": authentication.fingerprint_inputs,
            },
        }
    )


def hash_configuration(configuration: Mapping[str, object]) -> str:
    """Hash typed, deterministic inputs without retaining their serialization."""

    def canonical(value: object) -> object:
        if isinstance(value, SecretStr):
            return ["secret", value.get_secret_value()]
        if value is None or type(value) in (str, int, bool):
            return [type(value).__name__, value]
        if isinstance(value, Mapping):
            if not all(
                isinstance(key, str) for key in cast(Mapping[object, object], value)
            ):
                raise ConfigurationError("Fingerprint input keys must be strings.")
            mapping = cast(Mapping[str, object], value)
            return [
                "mapping",
                [[key, canonical(mapping[key])] for key in sorted(mapping)],
            ]
        if isinstance(value, (tuple, list)):
            return ["sequence", [canonical(item) for item in cast(list[object], value)]]
        raise ConfigurationError("Unsupported resolved destination fingerprint input.")

    encoded = json.dumps(
        canonical(configuration), ensure_ascii=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
