from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import duckdb
from pydantic import Field, PrivateAttr

from assquack._errors import ExportError
from assquack._exports.authentication.base import Authenticator
from assquack._exports.authentication.credentials import (
    CredentialProvider,
    EnvironmentCredentialProvider,
)
from assquack._exports.authentication.registry import (
    AuthenticationRegistry,
    default_authenticators,
)
from assquack._exports.destinations.base import (
    ExportDestination,
    clear_exception_locals,
)
from assquack._exports.destinations.fingerprint import destination_fingerprint
from assquack._exports.models import ExportTarget
from assquack._exports.preparation.registry import (
    PreparationRegistry,
    PreparationStrategy,
    default_preparations,
)
from assquack._exports.targets import parse_export_targets
from assquack._exports.writer.file import DuckDBFileWriter
from assquack._storage.tables import TableReference


class FileSystemDestination(ExportDestination):
    """Compose registered preparation and authentication, then write with DuckDB."""

    base_uri: str = Field(min_length=1)
    auth: str = "auto"
    _preparations: PreparationRegistry = PrivateAttr(
        default_factory=default_preparations
    )
    _authenticators: AuthenticationRegistry = PrivateAttr(
        default_factory=default_authenticators
    )
    _credential_provider: CredentialProvider = PrivateAttr(
        default_factory=EnvironmentCredentialProvider
    )

    def resolve(
        self, export: str | Mapping[str, str], *, asset_name: str
    ) -> tuple[ExportTarget, ...]:
        self._release()
        authentication: Authenticator | None = None
        try:
            targets = parse_export_targets(
                export, asset_name=asset_name, base_uri=self.base_uri
            )
            preparation = self._preparations.select(targets[0].resolved_uri)
            authentication = self._authenticators.select(
                preparation.kind, self.auth, self._credential_provider
            )
            targets = self._resolve_file_paths(targets, preparation, authentication)
            fingerprint = destination_fingerprint(
                self._resolved_configuration(), preparation, authentication
            )
            return self._capture_resolution(
                fingerprint, preparation, authentication, targets
            )
        except BaseException as error:
            clear_exception_locals(error)
            raise
        finally:
            del authentication

    def _resolve_file_paths(
        self,
        targets: tuple[ExportTarget, ...],
        preparation: PreparationStrategy,
        authentication: Authenticator,
    ) -> tuple[ExportTarget, ...]:
        """Resolve each pipe-expanded output to the exact path used by the writer."""
        try:
            return tuple(
                replace(
                    target,
                    resolved_uri=preparation.resolve_uri(
                        target.resolved_uri, authentication
                    ),
                )
                for target in targets
            )
        finally:
            del authentication

    def _resolved_configuration(self) -> dict[str, object]:
        """Normalize the destination base independently of individual output paths."""
        configuration = self.model_dump()
        if "://" not in self.base_uri:
            configuration["base_uri"] = str(Path(self.base_uri).resolve())
        return configuration

    def export(
        self,
        connection: duckdb.DuckDBPyConnection,
        source: TableReference,
        target: ExportTarget,
    ) -> None:
        state = self._resolved
        try:
            if state is None or not any(
                target is resolved for resolved in state.targets
            ):
                raise ExportError(
                    "Export target must belong to this destination's current resolve()."
                )
            with state.preparation.prepare(
                connection, target.resolved_uri, state.authentication
            ):
                DuckDBFileWriter().write(
                    connection, source, target, target.resolved_uri
                )
        except BaseException as error:
            self._release()
            clear_exception_locals(error)
            raise
        finally:
            if state is not None and target is state.targets[-1]:
                self._release()
            del state
