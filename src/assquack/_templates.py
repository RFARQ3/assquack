from __future__ import annotations

import inspect
import string
from typing import Any, overload


class AssetTemplateFormatter:
    """Render asset templates using bound argument context."""

    def __init__(
        self,
        format_args: tuple[Any, ...],
        bound_arguments: inspect.BoundArguments,
    ) -> None:
        self._format_args = format_args
        self._formatter = string.Formatter()
        self._format_kwargs = self._prepare_format_kwargs(bound_arguments)

    @property
    def format_kwargs(self) -> dict[str, Any]:
        """Return a shallow copy of the available template arguments."""

        return dict(self._format_kwargs)

    @overload
    def format(self, template: None, allow_partial: bool = False) -> None: ...

    @overload
    def format(self, template: str, allow_partial: bool = False) -> str: ...

    def format(
        self,
        template: str | None,
        allow_partial: bool = False,
    ) -> str | None:
        """Format known values while optionally preserving missing placeholders."""

        if template is None:
            return None

        formatted = template
        seen_templates: set[str] = set()

        while self.has_unresolved_fields(formatted):
            if formatted in seen_templates:
                if allow_partial:
                    break

                raise KeyError(
                    f"Asset template '{template}' could not be fully resolved."
                )

            seen_templates.add(formatted)
            formatted = self._format_once(
                formatted,
                template,
                allow_partial=allow_partial,
            )

        return formatted

    def has_unresolved_fields(self, template: str) -> bool:
        """Return whether a template still contains format placeholders."""

        return any(
            field_name is not None
            for _, field_name, _, _ in self._formatter.parse(template)
        )

    def _format_once(
        self,
        template: str,
        original_template: str,
        *,
        allow_partial: bool,
    ) -> str:
        if allow_partial:
            return template.format_map(_SafeFormatDict(self._format_kwargs))

        try:
            return template.format(*self._format_args, **self._format_kwargs)
        except KeyError as exc:
            missing_key = exc.args[0] if exc.args else "<unknown>"
            available_keys = ", ".join(sorted(self._format_kwargs)) or "<none>"
            raise KeyError(
                f"Missing format key '{missing_key}' while formatting asset template "
                f"'{original_template}'. Available keys: {available_keys}."
            ) from exc

    def _prepare_format_kwargs(
        self,
        bound_arguments: inspect.BoundArguments,
    ) -> dict[str, Any]:
        format_kwargs = dict(bound_arguments.arguments)
        nested_values: dict[str, Any] = {}
        seen_assets: set[int] = set()

        for value in format_kwargs.values():
            self._collect_nested_asset_arguments(value, nested_values, seen_assets)

        for key, value in nested_values.items():
            format_kwargs.setdefault(key, value)

        return format_kwargs

    def _collect_nested_asset_arguments(
        self,
        value: Any,
        accumulator: dict[str, Any],
        seen_assets: set[int],
    ) -> None:
        from assquack._api import AssquackAsset

        if not isinstance(value, AssquackAsset):
            return

        asset_object_id = id(value)
        if asset_object_id in seen_assets:
            return

        seen_assets.add(asset_object_id)
        nested_arguments = value._get_bound_arguments()

        for key, nested_value in nested_arguments.arguments.items():
            accumulator.setdefault(key, nested_value)
            self._collect_nested_asset_arguments(
                nested_value,
                accumulator,
                seen_assets,
            )


class _SafeFormatDict(dict[str, Any]):
    """Dictionary that leaves missing template keys unresolved."""

    def __missing__(self, key: str) -> str:
        return _Placeholder(key)


class _Placeholder(str):
    """String proxy that preserves dotted attribute and index access."""

    def __new__(cls, key: str) -> _Placeholder:
        return super().__new__(cls, "{" + key + "}")

    def __getattr__(self, item: str) -> _Placeholder:
        return _Placeholder(f"{self.strip('{}')}.{item}")

    def __getitem__(self, item: Any) -> _Placeholder:
        return _Placeholder(f"{self.strip('{}')}[{item}]")
