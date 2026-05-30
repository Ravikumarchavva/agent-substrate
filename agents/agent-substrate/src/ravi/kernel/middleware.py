"""Middleware (interceptor) contract."""

from __future__ import annotations

from typing import Protocol

from ravi.kernel.message import Message


class Interceptor(Protocol):
    """Middleware component that intercepts messages pre- and post-delivery.

    Can mutate messages, raise to halt execution (e.g. for guardrails),
    or record state asynchronously (e.g. audit logging).
    """

    async def pre_process(self, message: Message) -> Message:
        """Called before the message reaches the agent."""
        return message

    async def post_process(self, message: Message) -> Message:
        """Called before the message is sent out by the agent."""
        return message
