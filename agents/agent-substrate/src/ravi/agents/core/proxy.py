"""UserProxyAgent — bridges external callers into the actor runtime.

Every multi-agent interaction starts here.  External callers (HTTP routes,
CLI, tests) create a UserProxyAgent and use ``ask()`` to send tasks into
the runtime, where a registered agent (typically ReActAgent) handles them.

    runtime = LocalRuntime()
    await runtime.start()

    agent = ReActAgent("assistant", runtime, model=llm)
    await runtime.register(agent.id, agent.on_message)

    proxy = UserProxyAgent("proxy", runtime)
    result = await proxy.ask("What is 2+2?", recipient=agent.id)
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Callable

from ravi.kernel import AgentId, AgentRuntime, MessageContext, TextBlock
from ravi.kernel.stream import CompletionEvent, StreamDone
from ravi.logger import setup_logging

logger = setup_logging()


class UserProxyAgent:
    """Routes external requests to registered agents via the runtime.

    Parameters
    ----------
    name:
        Agent type name.
    runtime:
        The runtime this proxy dispatches through.
    key:
        Instance key — use unique keys for per-request proxies.
    hitl_callback:
        Optional async callable invoked when a reverse message arrives
        (e.g. an ReActAgent requesting human input).
        Signature: ``async (ctx: MessageContext, payload: object) -> object``.
    """

    def __init__(
        self,
        name: str,
        runtime: AgentRuntime,
        *,
        key: str = "default",
        hitl_callback: Callable[[MessageContext, object], Any] | None = None,
    ) -> None:
        self.name = name
        self.runtime = runtime
        self.id = AgentId(type="proxy", key=key)
        self._hitl_callback = hitl_callback

    # -- One-shot ask --------------------------------------------------------

    async def ask(self, text: str, *, recipient: AgentId) -> object:
        """Send *text* to *recipient* and return the complete response.

        Returns whatever the recipient's ``on_message()`` returns — typically
        an ``AgentRunResult`` or a plain string.
        """
        return await self.runtime.send_message(text, recipient=recipient)

    # -- Streaming ask -------------------------------------------------------

    async def ask_stream(
        self, text: str, *, recipient: AgentId
    ) -> AsyncIterator[CompletionEvent | StreamDone]:
        """Send *text* to *recipient* and yield a streaming response.

        Calls ``ask()`` and wraps the result in a single ``CompletionEvent``
        followed by ``StreamDone``.  Full topic-based streaming requires a
        ``LocalRuntime`` with pub/sub support (Pass 6).  # TODO(pass-6-runtime)
        """
        result = await self.ask(text, recipient=recipient)
        output = ""
        if hasattr(result, "output"):
            output = str(result.output)
        elif isinstance(result, str):
            output = result
        elif result is not None:
            output = str(result)
        yield CompletionEvent(content=[TextBlock(text=output)])
        yield StreamDone()

    # -- Reverse message handling (HITL) ------------------------------------

    async def on_message(self, ctx: MessageContext, payload: object) -> object:
        """Handle messages sent TO this proxy (e.g. HITL clarification requests).

        If a ``hitl_callback`` was provided it is invoked; otherwise returns None.
        """
        if self._hitl_callback is not None:
            return await self._hitl_callback(ctx, payload)
        return None
