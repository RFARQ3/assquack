from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from assquack._config import AssquackConfig

_QUERY_CONFIG: ContextVar[AssquackConfig | None] = ContextVar(
    "assquack_query_config", default=None
)

_EXPLICIT_CONFIG: ContextVar[AssquackConfig | None] = ContextVar(
    "assquack_explicit_query_config", default=None
)


@contextmanager
def query_context(config: AssquackConfig) -> Generator[None, None, None]:
    token = _QUERY_CONFIG.set(config)
    try:
        yield
    finally:
        _QUERY_CONFIG.reset(token)


def current_query_config() -> AssquackConfig | None:
    return _EXPLICIT_CONFIG.get() or _QUERY_CONFIG.get()


@contextmanager
def use_config(config: AssquackConfig) -> Generator[None, None, None]:
    """Select the database for general queries within this task-local scope."""
    if not isinstance(config, AssquackConfig):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TypeError("assquack.use_config requires an AssquackConfig.")
    token = _EXPLICIT_CONFIG.set(config)
    try:
        yield
    finally:
        _EXPLICIT_CONFIG.reset(token)
