"""Interceptor re-export + MiddlewarePipeline concrete impl."""

from __future__ import annotations

from ravi.kernel import Message
from ravi.kernel.middleware import Interceptor


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


__all__ = ["Interceptor", "MiddlewarePipeline"]
