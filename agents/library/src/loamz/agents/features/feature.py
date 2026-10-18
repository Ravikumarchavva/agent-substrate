"""What a feature declares to the server: its router, the roles that serve it and the events it reacts to."""

from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import APIRouter

from loamz.agents.kernel.events import DomainEvent

if TYPE_CHECKING:
    from loamz.agents.bootstrap.container import Container

Handler = Callable[["Container", DomainEvent], Awaitable[None]]


@dataclass(frozen=True)
class Subscription:
    """React to one catalog event: delivered once to this feature's consumer `group`."""

    event_type: str
    handler: Handler


@dataclass(frozen=True)
class Feature:
    name: str
    """`<context>.<feature>`, the folder under `features/`."""
    roles: frozenset[str]
    """The processes that serve it: gateway, runtime, tools, jobs, admin (`all` serves every one)."""
    subscriptions: Sequence[Subscription] = ()

    @property
    def module(self) -> str:
        context, feature = self.name.split(".")
        return f"loamz.agents.features.{context}.{feature}"

    def service(self, container: Container) -> object:
        """The feature's service (its provider function is named like the feature: `service.preferences`)."""
        module = importlib.import_module(f"{self.module}.service")
        return container.get(getattr(module, self.name.split(".")[1]))

    def router(self) -> APIRouter:
        """The feature's `api.router`, imported when the server asks (only the server loads `api.py`)."""
        return importlib.import_module(f"{self.module}.api").router


def feature(name: str, *roles: str, subscriptions: Sequence[Subscription] = ()) -> Feature:
    return Feature(name, frozenset(roles), subscriptions)


__all__ = ["Feature", "Handler", "Subscription", "feature"]
