"""UserProxyAgent — bridges external callers into the actor fabric.

Every multi-agent interaction starts here.  Instead of calling agent.run()
directly, external callers (HTTP routes, CLI, tests) create a UserProxyAgent,
register it with the runtime, and use ask() / ask_stream() to enter the fabric.

    async with LocalRuntime() as runtime:
        assistant = AssistantAgent("assistant", runtime, catalog=catalog)
        await assistant.start()

        proxy = UserProxyAgent("proxy", runtime)
        await proxy.start()

        # One-shot:
        result = await proxy.ask("What is 2+2?", recipient=assistant.id)

        # Streaming — caller provides a StreamChannel (e.g. a translating adapter):
        channel = MyStreamChannel()
        await proxy.ask_stream("Tell me a story", recipient=assistant.id, channel=channel)
"""

from __future__ import annotations

from typing import Any, Callable, Optional, List

from ravi.fabric.actors.actor import ActorAgent, StreamEnvelope, StreamChannel
from ravi.kernel import ContentBlock, MessageContext, AgentId, TopicId, AgentRuntime


class UserProxyAgent(ActorAgent):
    """External-caller proxy in the actor fabric.

    Parameters
    ----------
    name:
        Agent type name.
    runtime:
        The runtime this proxy registers with.
    key:
        Instance key. Use unique keys when creating per-request proxies.
    hitl_callback:
        Optional async callable invoked when a reverse message arrives
        (e.g. an AssistantAgent asking the human for input).
        Signature: ``async (ctx, content) -> object``.
    subscriptions:
        Topics to subscribe to on start.
    """

    def __init__(
        self,
        name: str,
        runtime: AgentRuntime,
        *,
        key: str = "default",
        hitl_callback: Optional[Callable[[MessageContext, list[ContentBlock]], Any]] = None,
        subscriptions: Optional[List[TopicId]] = None,
    ) -> None:
        super().__init__(
            name=name,
            runtime=runtime,
            key=key,
            description="User proxy agent",
            subscriptions=subscriptions,
        )
        self._hitl_callback = hitl_callback

    # -- One-shot ask --------------------------------------------------------

    async def ask(self, text: str, *, recipient: AgentId) -> object:
        """Send a task and await the complete response.

        Parameters
        ----------
        text:
            The task / prompt to send.
        recipient:
            ``AgentId`` of the target agent.

        Returns
        -------
        object
            Whatever the recipient's ``on_message()`` returns (typically an
            ``AgentRunResult`` or a plain string).
        """
        return await self.send(text, recipient=recipient)

    # -- Streaming ask -------------------------------------------------------

    async def ask_stream(
        self,
        text: str,
        *,
        recipient: AgentId,
        channel: StreamChannel,
    ) -> None:
        """Send a task and stream the response through ``channel``.

        The method returns immediately; the agent runs in a background task
        and emits chunks to ``channel``.  The caller iterates the channel to
        consume events.

        Parameters
        ----------
        text:
            The task / prompt.
        recipient:
            Target agent's ``AgentId``.
        channel:
            A ``StreamChannel`` implementation (e.g. the server layer's
            translating adapter wrapping an ``EventBus``).  The agent will
            call ``channel.emit(chunk)`` for each streaming chunk and
            ``channel.close()`` when done.

        Example::

            channel = MyTranslatingChannel(EventBus())
            asyncio.ensure_future(
                proxy.ask_stream("tell me a story", recipient=agent.id, channel=channel)
            )
            async for event in channel.bus:
                yield f"data: {event.to_sse()}\\n\\n"
        """
        envelope = StreamEnvelope(task=text, channel=channel)
        await self.send(envelope, recipient=recipient)

    # -- Reverse message handling (HITL) ------------------------------------

    async def on_message(
        self, ctx: MessageContext, content: list[ContentBlock]
    ) -> object:
        """Handle messages sent TO this proxy (e.g. HITL clarification requests).

        If a ``hitl_callback`` was provided, it is invoked.
        Otherwise returns None silently.
        """
        if self._hitl_callback is not None:
            return await self._hitl_callback(ctx, content)
        return None
