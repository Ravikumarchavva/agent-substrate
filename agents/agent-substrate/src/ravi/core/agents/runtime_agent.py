"""Runtime-aware agent base — Layer 2 on top of the core actor runtime.

This module provides ``RuntimeAgent``, a lightweight class that bridges the
raw core runtime primitives (mailboxes, dispatcher, topics) with a clean,
declarative agent API.

Layer 1 (core.runtime): Raw actor primitives — register, subscribe, send, publish.
Layer 2 (this module):  Declarative agent class — auto-registers, subscribes,
                        and routes messages through a simple ``on_message()`` hook.

Usage::

    class GreeterAgent(RuntimeAgent):
        async def on_message(self, ctx: MessageContext, content: list[ContentBlock]) -> object:
            text = content[0].text if content else ""
            return f"Hello, {text}!"

    runtime = LocalRuntime()
    await runtime.start()
    greeter = GreeterAgent(name="greeter", runtime=runtime)
    await greeter.start()

    result = await runtime.send_message("World", recipient=greeter.id)
    # result == "Hello, World!"
"""

from __future__ import annotations

import logging
from typing import Optional

from ravi.core.messages.content import ContentBlock
from ravi.core.runtime._identity import AgentId, TopicId
from ravi.core.runtime._contracts import MessageContext
from ravi.core.runtime._protocol import AgentRuntime
from ravi.core.tools.base_tool import BaseTool

from ravi.core.catalog import AgentCatalogRegistry

logger = logging.getLogger("ravi.core.agents.runtime_agent")


class RuntimeAgent:
    """Declarative agent that auto-registers with the core runtime.

    Subclass this and override ``on_message()`` to handle incoming messages.
    Call ``start()`` to register with the runtime and ``stop()`` to unregister.

    Parameters
    ----------
    name:
        Agent type name (used for registration and addressing).
    runtime:
        The ``AgentRuntime`` instance to register with.
    key:
        Instance key for the ``AgentId``.  Defaults to ``"default"``.
    description:
        Human-readable description of what this agent does.
    tools:
        Optional list of tools this agent can use.
    subscriptions:
        Optional list of ``TopicId`` to subscribe to on start.
    catalog:
        Optional unified capability catalog.
    """

    def __init__(
        self,
        name: str,
        runtime: AgentRuntime,
        *,
        key: str = "default",
        description: str = "",
        tools: Optional[list[BaseTool]] = None,
        subscriptions: Optional[list[TopicId]] = None,
        catalog: Optional[AgentCatalogRegistry] = None,
    ) -> None:
        self.name = name
        self.runtime = runtime
        self.key = key
        self.description = description or f"{name} agent"
        self.catalog = catalog or AgentCatalogRegistry.from_tools_and_skills(tools or [])
        self._subscriptions: list[TopicId] = list(subscriptions) if subscriptions else []
        self._started = False

    @property
    def tools(self) -> list[BaseTool]:
        """Dynamically fetch all tools registered in the unified capability catalog."""
        return self.catalog.all_tools()

    @tools.setter
    def tools(self, value: list[BaseTool]) -> None:
        """Replace tools in the unified capability catalog."""
        # Unregister all current tools
        for t in self.catalog.all_tools():
            self.catalog.unregister(t.name)
        # Register new tools
        for t in value:
            self.catalog.register_tool(t)

    @property
    def id(self) -> AgentId:
        """The unique ``AgentId`` for this agent instance."""
        return AgentId(type=self.name, key=self.key)

    # -- Lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Register this agent with the runtime and subscribe to topics.

        After calling ``start()``, the agent is live and will receive messages
        addressed to its ``AgentId`` or published to its subscribed topics.
        """
        if self._started:
            return

        # Register handler with runtime
        await self.runtime.register(self.name, self._dispatch)

        # Subscribe to declared topics
        for topic in self._subscriptions:
            await self.runtime.subscribe(self.name, topic)

        self._started = True
        logger.info("RuntimeAgent '%s' started (id=%s)", self.name, self.id)

    async def stop(self) -> None:
        """Mark the agent as stopped."""
        self._started = False
        logger.info("RuntimeAgent '%s' stopped", self.name)

    # -- Message handling (override this) ------------------------------------

    async def on_message(
        self, ctx: MessageContext, content: list[ContentBlock]
    ) -> object:
        """Handle an incoming message.  Override in subclasses.

        Parameters
        ----------
        ctx:
            Message context with ``ctx.runtime``, ``ctx.sender``, ``ctx.agent_id``.
        content:
            The multimodal content blocks from the envelope.

        Returns
        -------
        object
            The response value sent back to the caller (for ``send_message``).
            For pub-sub messages, the return value is typically ignored.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__}.on_message() not implemented"
        )

    # -- Convenience helpers -------------------------------------------------

    async def send(
        self,
        message: str | list[ContentBlock],
        *,
        recipient: AgentId,
    ) -> object:
        """Send a point-to-point message to another agent.

        Shorthand for ``self.runtime.send_message(..., sender=self.id, ...)``.
        """
        return await self.runtime.send_message(
            message,
            sender=self.id,
            recipient=recipient,
        )

    async def publish(
        self,
        message: str | list[ContentBlock],
        *,
        topic: TopicId,
    ) -> None:
        """Publish a message to a topic.

        Shorthand for ``self.runtime.publish_message(..., sender=self.id, ...)``.
        """
        await self.runtime.publish_message(
            message,
            sender=self.id,
            topic=topic,
        )

    def get_tool(self, name: str) -> Optional[BaseTool]:
        """Look up a tool by name from this agent's catalog."""
        return self.catalog.get_tool(name)

    # -- Internal dispatch ---------------------------------------------------

    async def _dispatch(
        self, ctx: MessageContext, content: list[ContentBlock]
    ) -> object:
        """Internal dispatcher that routes to ``on_message()``."""
        return await self.on_message(ctx, content)

    # -- Repr ----------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}("
            f"name={self.name!r}, key={self.key!r}, "
            f"tools={len(self.tools)}, subs={len(self._subscriptions)}"
            f")>"
        )
