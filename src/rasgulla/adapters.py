"""Provider-neutral model adapter interface.

The default adapter is local and deterministic: it needs no API key and makes
no network call. Another provider can be plugged in by implementing the
``ModelAdapter`` protocol and registering it.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = [
    "ADAPTERS",
    "ModelAdapter",
    "NullAdapter",
    "StaticAdapter",
    "available_adapters",
    "get_adapter",
]


@runtime_checkable
class ModelAdapter(Protocol):
    """The interface every model adapter implements."""

    name: str

    def generate_test_plan(self, findings) -> list[dict]:
        """Return a test plan for the given findings."""
        ...


class StaticAdapter:
    """Deterministic local adapter: no model, no network, no API key."""

    name = "static"

    def generate_test_plan(self, findings) -> list[dict]:
        from .testplan import build_test_plan

        return build_test_plan(findings)


class NullAdapter:
    """Adapter that produces no test plan."""

    name = "null"

    def generate_test_plan(self, findings) -> list[dict]:
        return []


ADAPTERS = {
    "static": StaticAdapter,
    "null": NullAdapter,
}


def available_adapters() -> list[str]:
    """Return the names of the registered adapters."""
    return sorted(ADAPTERS)


def get_adapter(name: str = "static") -> ModelAdapter:
    """Return the adapter registered under ``name``."""
    key = (name or "static").strip().lower()
    if key not in ADAPTERS:
        raise ValueError(
            f"unknown adapter {name!r}; available adapters: {', '.join(available_adapters())}"
        )
    return ADAPTERS[key]()
