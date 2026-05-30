from __future__ import annotations

from typing import Protocol

from ravi.kernel import Message


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


class MiddlewarePipeline:
    """Executes a chain of Interceptors in registration order."""

    def __init__(self, interceptors: list[Interceptor] | None = None) -> None:
        self.interceptors: list[Interceptor] = list(interceptors or [])

    def add(self, interceptor: Interceptor) -> None:
        self.interceptors.append(interceptor)

    async def execute_pre(self, message: Message) -> Message:
        for interceptor in self.interceptors:
            message = await interceptor.pre_process(message)
        return message

    async def execute_post(self, message: Message) -> Message:
        for interceptor in self.interceptors:
            message = await interceptor.post_process(message)
        return message
