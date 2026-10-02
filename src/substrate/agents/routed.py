"""The routed agent: handlers declared by payload type, dispatch owned by the base.

::

    class Support(RoutedAgent):
        @handle(ChatPayload)
        async def on_chat(self, ctx: RunContext, msg: Message) -> None: ...

        @handle(TicketPayload)
        async def on_ticket(self, ctx: RunContext, msg: Message) -> None: ...

The engine hands an agent a batch of messages (``run(ctx, inbox)``). A routed agent turns
that into one handler call per message: it checks for cancellation between messages, opens
a ``substrate.handler`` span around each, and chooses the handler from the payload's type.
A payload no handler accepts is a typed, non-retryable ``UnroutableMessageError`` rather
than a silent drop — redelivering a poison message cannot fix it.

Subclasses inherit their parents' handlers and may override one by declaring a handler for
the same payload type.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar

from substrate.types.errors import UnroutableMessageError
from substrate.runtime.message import Message
from substrate.telemetry import semconv
from substrate.telemetry.tracing import span

if TYPE_CHECKING:
    from substrate.runtime.context import RunContext

Handler = Callable[[Any, "RunContext", Message], Awaitable[None]]
F = TypeVar("F", bound=Callable[..., Awaitable[None]])

_ACCEPTS = "_handles_payloads"


def handle(*payload_types: type) -> Callable[[F], F]:
    """Mark a method as the handler for messages whose payload is one of ``payload_types``."""
    if not payload_types:
        raise TypeError("handle() needs at least one payload type")

    def mark(fn: F) -> F:
        setattr(fn, _ACCEPTS, payload_types)
        return fn

    return mark


class RoutedAgent:
    """Base for agents that dispatch by payload type. Subclasses set ``id`` and declare handlers."""

    id: Any
    version: str = "0"
    """What a run is pinned to when it starts: the worker will not continue it under a
    different one (see ``Worker``). Override when behaviour changes incompatibly."""

    _routes: ClassVar[dict[type, str]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        routes: dict[type, str] = {}
        for klass in reversed(cls.__mro__):
            for name, member in vars(klass).items():
                for payload_type in getattr(member, _ACCEPTS, ()):
                    routes[payload_type] = name
        cls._routes = routes

    def _handler_for(self, msg: Message) -> Handler:
        payload_type = type(msg.payload)
        for accepted, name in self._routes.items():
            if isinstance(msg.payload, accepted):
                return getattr(self, name)  # type: ignore[no-any-return]
        accepts = tuple(sorted(t.__name__ for t in self._routes))
        raise UnroutableMessageError(
            f"{self.id} has no handler for {payload_type.__name__} (accepts: {', '.join(accepts) or 'nothing'})",
            agent=str(self.id),
            payload_type=payload_type.__name__,
            accepts=accepts,
        )

    async def run(self, ctx: "RunContext", inbox: list[Message]) -> None:
        for msg in inbox:
            ctx.check()
            handler = self._handler_for(msg)
            attributes = {semconv.RUN_AGENT: str(self.id), "substrate.handler.payload": type(msg.payload).__name__}
            with span(semconv.SPAN_HANDLER, attributes=attributes):
                await handler(ctx, msg)


__all__ = ["RoutedAgent", "handle"]
