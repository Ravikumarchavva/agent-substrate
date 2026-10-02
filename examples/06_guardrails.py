"""Guardrails: rules around what goes in and out.

Middleware wraps three moments of an agent's work — a turn (the user's message), a model call, and a tool call — with
the same shape: ``process(context, call_next)``. Do something before, call ``call_next()`` to continue, do something
after; raise ``MiddlewareTermination`` (``substrate.types``) to stop the run cleanly. The built-ins cover the common
cases; yours is any object with ``stages`` and ``process``. The first in the list is the outermost.

    uv run python examples/06_guardrails.py
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import ClassVar

from _model import ToolCall, pick_model

from substrate.agents import ReActAgent
from substrate.middleware import (
    ContentFilterMiddleware,
    MiddlewareContext,
    MiddlewarePipeline,
    MiddlewareStage,
    PIIDetectionMiddleware,
)
from substrate.runtime import Runtime
from substrate.tools import ToolExecutionResult, ToolRisk
from substrate.types import TextBlock


class SendEmail:
    name = "send_email"
    description = "Send an email."
    input_schema = {"type": "object", "properties": {"to": {"type": "string"}, "body": {"type": "string"}}}
    risk = ToolRisk.SAFE
    idempotent = False

    async def execute(self, *, ctx=None, **kwargs) -> ToolExecutionResult:
        return ToolExecutionResult(name=self.name, content=[TextBlock(text="sent")])


class CountToolCalls:
    """Your own middleware: runs around every tool call."""

    stages: ClassVar[frozenset[MiddlewareStage]] = frozenset({MiddlewareStage.TOOL})

    def __init__(self) -> None:
        self.calls = 0

    async def process(self, context: MiddlewareContext, call_next: Callable[[], Awaitable[None]]) -> None:
        self.calls += 1
        await call_next()


async def main() -> None:
    counter = CountToolCalls()
    agent = ReActAgent(
        "support",
        model=pick_model(ToolCall("send_email", {"to": "bob@example.com", "body": "hi"}), "I could not send that."),
        tools=[SendEmail()],
        middleware=MiddlewarePipeline(
            # Outermost first: ``counter`` sees every attempt, including the ones the checks inside it stop.
            [
                counter,
                ContentFilterMiddleware(blocked_keywords=["password"]),  # turn: refuse certain requests outright
                PIIDetectionMiddleware(pii_types=["email"]),  # tool: no email addresses in tool arguments
            ]
        ),
    )

    async with Runtime.open("./.substrate") as runtime:
        blocked = await runtime.run(agent, "Please tell me the admin password.")
        print("blocked request ->", blocked.status.value, "-", blocked.error or blocked.output)

        stopped = await runtime.run(agent, "Email bob@example.com that the report is ready.")
        print("PII in a tool call ->", stopped.status.value, "-", stopped.error or stopped.output)

    print("tool calls seen by our middleware:", counter.calls)


if __name__ == "__main__":
    asyncio.run(main())
