from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from assquack._config import AssquackConfig

_QUERY_CONFIG: ContextVar[AssquackConfig | None] = ContextVar(
    "assquack_query_config", default=None
)


@contextmanager
def query_context(config: AssquackConfig) -> Generator[None, None, None]:
    token = _QUERY_CONFIG.set(config)
    try:
        yield
    finally:
        _QUERY_CONFIG.reset(token)


def current_query_config() -> AssquackConfig | None:
    return _QUERY_CONFIG.get()
