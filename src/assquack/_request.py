from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from assquack._config import AssquackConfig
from assquack._options import AssetMode, ExportSpec


@dataclass(frozen=True, slots=True)
class AssquackRequest:
    """Hold bound arguments and declared options for an executing asset call.

    Arguments and configuration retain their ordinary Python references. Resolve
    calculates identity before producer code can mutate the argument values.
    """

    fn: Callable[..., Any]
    arguments: tuple[Any, ...]
    keyword_arguments: Mapping[str, Any]
    table_template: str
    name_template: str | None
    export_template: str | ExportSpec | None
    mode: AssetMode
    config: AssquackConfig
    use_cache: bool = False
    cache_expiration: timedelta | None = None
